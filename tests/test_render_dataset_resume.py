"""Exercise durable stage recovery and scheduling without launching Unreal."""
import csv
import hashlib
import json
from pathlib import Path
import shutil
import sys
import threading
import time
from types import SimpleNamespace

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'Plugins/MatrixCityPlugin/misc'))
sys.path.insert(0, str(ROOT / 'scripts'))
import dataset_plan
import gpu_devices
import render_dataset as renderer
import render_resume
from run_cmd_async import EXIT_TEARDOWN_INCOMPLETE, EXIT_WORKER_UNREAPED
import camera_json_protocol as protocol
import render_forest_dataset as forest
from test_camera_json_export import make_pair, payload


@pytest.fixture
def pipeline(tmp_path, monkeypatch):
    project = tmp_path / 'project'
    (project / 'Content/Map').mkdir(parents=True)
    (project / 'Content/Map/scene_fixture.umap').touch()
    uproject = project / 'fixture.uproject'
    uproject.write_text('{}')
    data = project / 'Config/Dataset'
    data.mkdir(parents=True)
    (data / 'dataset_manifest.json').write_text('{}')
    map_path = '/Game/Map/scene_fixture'
    manifest = {'maps': [{'map': map_path, 'split': 'train', 'source_scene': 'fixture',
                          'targets': ['Target_001'], 'occlusion_actor_names': []}]}
    with (data / 'train_sequences.csv').open('w') as f:
        columns = ['sequence_id', 'map_path', 'split', 'source_scene', 'target_label',
                   'expected_views', 'trajectory', 'height_m', 'yaw_deg']
        writer = csv.DictWriter(f, columns)
        writer.writeheader()
        for i, angle in enumerate((0, 120)):
            writer.writerow(dict(zip(columns, [f'seq_{i}', map_path, 'train', 'fixture',
                'Target_001', 25, 'plane_grid', 50, angle])))
    monkeypatch.setattr(dataset_plan, 'installed_manifest', lambda *_: (manifest, {'manifest_sha256': 'fixture'}))
    trajectory = {'global': {'trajectory_types': ['plane_grid'], 'camera_heights': [5000.0],
                             'plane_angles': [0, 120], 'num_frames': 25}}
    render = {'Output_Path': str(tmp_path / 'outputs'), 'Resolution': [512, 512],
              'Render_Passes': [{'pass_name': 'rgb', 'ext': 'png', 'enable': True},
                                {'pass_name': 'depth', 'ext': 'exr', 'enable': True}]}
    trajectory_path = tmp_path / 'trajectory.yaml'
    render_path = tmp_path / 'render.yaml'
    trajectory_path.write_text(yaml.safe_dump(trajectory))
    render_path.write_text(yaml.safe_dump(render))
    config = {'ue_project': str(uproject), 'ue_command': sys.executable,
              'python_script': str(tmp_path / 'pipeline.py'), 'render_config': str(render_path),
              'maps': [map_path]}
    config_path = tmp_path / 'config.json'
    config_path.write_text(json.dumps(config))
    run = tmp_path / 'run'
    output = tmp_path / 'outputs/train'
    events = []
    behavior = SimpleNamespace(camera_fail=False, partial_fail=False, render_rc=None)

    class Process:
        pid = 99999999

        def __init__(self, command, **_):
            self.returncode = 0
            if command[1].endswith('run_cmd_async.py'):
                worker = json.loads(Path(command[command.index('--worker-config') + 1]).read_text())
                tasks = worker['trajectory_tasks']
                events.append(('render', list(tasks), command[command.index('--gpu_id') + 1]))
                for kind, height, angle in tasks[:1] if behavior.partial_fail else tasks:
                    prefix = 'scene_fixture_Target_001_height_{:03d}_ang_{:03d}'.format(int(height/100), int(angle))
                    make_pair(output, kind, prefix)
                if behavior.partial_fail:
                    self.returncode = 7
                if behavior.render_rc is not None:
                    self.returncode = behavior.render_rc
            else:
                assert command[1].endswith('export_camera_json.py')
                jobs = protocol.load_jobs_manifest(command[command.index('--jobs-file') + 1], output)
                events.append(('camera', len(jobs)))
                if behavior.camera_fail:
                    self.returncode = 8
                else:
                    for job in jobs:
                        protocol.write_pair(job, payload(job), overwrite=True)

        def wait(self, timeout=None):
            return self.returncode

        def poll(self):
            return self.returncode

    monkeypatch.setattr(renderer.subprocess, 'Popen', Process)
    monkeypatch.setattr(gpu_devices, 'gpu_inventory', lambda: [
        {'index': index, 'uuid': f'GPU-{index:08d}', 'memory_used_mb': 0,
         'memory_total_mb': 49152, 'utilization_gpu_percent': 0} for index in (0, 1)])
    monkeypatch.setattr('export_camera_json.blender_binary', lambda *_: '/fixture/blender')
    initial = ['--config', str(config_path), '--gpu-ids', '0', '--workers-per-gpu', '1',
               '--run-dir', str(run), '--trajectory-config', str(trajectory_path),
               '--shards-per-scene', '1', '--sync-profile', 'balanced']
    resume = ['--config', str(config_path), '--gpu-ids', '0,1', '--workers-per-gpu', '1',
              '--run-dir', str(run), '--resume']
    return SimpleNamespace(initial=initial, resume=resume, run=run, output=output,
                           events=events, behavior=behavior, process_class=Process)


def test_resume_preserves_completed_totals_and_allows_gpu_count_changes(pipeline):
    assert renderer.main(pipeline.initial) == 0
    pipeline.events.clear()
    assert renderer.main(pipeline.resume) == 0
    assert pipeline.events == []
    result = json.loads((pipeline.run / 'result.json').read_text())
    assert result['completed'] == result['resumed'] == 1
    assert result['completed_this_run'] == 0
    plan = json.loads((pipeline.run / 'plan.json').read_text())
    assert plan['total_workers'] == 2 and len(plan['jobs']) == 1


def test_missing_json_only_backfills_camera_without_unreal(pipeline):
    assert renderer.main(pipeline.initial) == 0
    next(pipeline.output.glob('plane_grid/*_occ/pose/transforms.json')).unlink()
    pipeline.events.clear()
    assert renderer.main(pipeline.resume) == 0
    assert pipeline.events == [('camera', 1)]


def test_malformed_camera_json_is_backfilled_without_unreal(pipeline):
    assert renderer.main(pipeline.initial) == 0
    next(pipeline.output.glob('plane_grid/*_occ/pose/transforms.json')).write_text('[]')
    pipeline.events.clear()
    assert renderer.main(pipeline.resume) == 0
    assert pipeline.events == [('camera', 1)]


def test_scheduler_dispatches_disjoint_shards_on_both_gpus(pipeline, monkeypatch):
    barrier = threading.Barrier(2)

    class Child(pipeline.process_class):
        def __init__(self, command, **kwargs):
            self.render = command[1].endswith('run_cmd_async.py')
            super().__init__(command, **kwargs)

        def wait(self, timeout=None):
            if self.render:
                barrier.wait(timeout=5)
            return super().wait(timeout)

    monkeypatch.setattr(renderer.subprocess, 'Popen', Child)
    assert renderer.main(pipeline.initial + ['--gpu-ids', '0,1', '--shards-per-scene', '2']) == 0
    events = [event for event in pipeline.events if event[0] == 'render']
    assert {event[2] for event in events} == {'0', '1'}
    assert len(events) == 2 and all(len(event[1]) == 1 for event in events)


@pytest.mark.parametrize('missing', ['frame', 'fbx'])
def test_missing_render_artifact_rerenders_only_affected_sequence(pipeline, missing):
    assert renderer.main(pipeline.initial) == 0
    path = (next(pipeline.output.glob('plane_grid/*_ang_120_GT/rgb/0004.png')) if missing == 'frame'
            else next(pipeline.output.glob('Exported_FBX/plane_grid/*_ang_120.fbx')))
    path.unlink()
    pipeline.events.clear()
    assert renderer.main(pipeline.resume) == 0
    rendered = [e for e in pipeline.events if e[0] == 'render']
    assert len(rendered) == 1 and rendered[0][1] == [["plane_grid", 5000.0, 120.0]]


def test_incomplete_sequence_is_deleted_before_rerender(pipeline):
    assert renderer.main(pipeline.initial) == 0
    folder = next(pipeline.output.glob('plane_grid/*_ang_120_GT'))
    (folder / 'rgb/0004.png').unlink()
    stale = folder / 'rgb/0025.png'
    stale.write_bytes(b'stale extra view')
    pipeline.events.clear()
    assert renderer.main(pipeline.resume) == 0
    rendered = [e for e in pipeline.events if e[0] == 'render']
    assert len(rendered) == 1 and rendered[0][1] == [["plane_grid", 5000.0, 120.0]]
    assert not stale.exists()
    assert len(list(folder.glob('rgb/*.png'))) == 25


def test_camera_only_backfill_is_not_limited_by_gpu_slots(pipeline, monkeypatch):
    assert renderer.main(pipeline.initial + ['--shards-per-scene', '2']) == 0
    for path in pipeline.output.glob('plane_grid/*_occ/pose/transforms.json'):
        path.unlink()
    barrier = threading.Barrier(2)

    class Child(pipeline.process_class):
        def wait(self, timeout=None):
            # Both exporters must run at once although only one GPU slot exists.
            barrier.wait(timeout=5)
            return super().wait(timeout)

    monkeypatch.setattr(renderer.subprocess, 'Popen', Child)
    pipeline.events.clear()
    resume = [value for value in pipeline.resume]
    resume[resume.index('0,1')] = '0'
    assert renderer.main(resume + ['--camera-export-workers', '2']) == 0
    assert pipeline.events == [('camera', 1), ('camera', 1)]


def test_camera_export_failure_keeps_render_stage_for_retry(pipeline):
    pipeline.behavior.camera_fail = True
    assert renderer.main(pipeline.initial) == 1
    assert list(pipeline.run.glob('*/render_done.json'))
    assert not list(pipeline.run.glob('*/done.json'))
    pipeline.behavior.camera_fail = False
    pipeline.events.clear()
    assert renderer.main(pipeline.resume) == 0
    assert pipeline.events == [('camera', 2)]


def test_interrupted_shard_reuses_complete_sequence_and_repairs_remaining(pipeline):
    pipeline.behavior.partial_fail = True
    assert renderer.main(pipeline.initial + ['--max-retries', '0']) == 1
    pipeline.behavior.partial_fail = False
    pipeline.events.clear()
    assert renderer.main(pipeline.resume) == 0
    assert [e[1] for e in pipeline.events if e[0] == 'render'] == [[['plane_grid', 5000.0, 120.0]]]
    assert [e for e in pipeline.events if e[0] == 'camera'] == [('camera', 2)]


def test_in_run_retry_renders_only_remaining_sequences(pipeline):
    pipeline.behavior.partial_fail = True
    assert renderer.main(pipeline.initial + ['--launch-stagger', '0']) == 0
    rendered = [e[1] for e in pipeline.events if e[0] == 'render']
    assert rendered == [[['plane_grid', 5000.0, 0.0], ['plane_grid', 5000.0, 120.0]],
                        [['plane_grid', 5000.0, 120.0]]]
    assert json.loads((pipeline.run / 'result.json').read_text())['completed'] == 1


def test_teardown_incomplete_with_valid_outputs_completes_without_retry(pipeline):
    pipeline.behavior.render_rc = EXIT_TEARDOWN_INCOMPLETE
    assert renderer.main(pipeline.initial + ['--max-retries', '0']) == 0
    assert len([e for e in pipeline.events if e[0] == 'render']) == 1
    result = json.loads((pipeline.run / 'result.json').read_text())
    assert result['completed'] == 1 and result['results'][0]['teardown_incomplete']


def test_teardown_incomplete_with_missing_outputs_is_retried(pipeline):
    pipeline.behavior.partial_fail = True
    pipeline.behavior.render_rc = EXIT_TEARDOWN_INCOMPLETE
    assert renderer.main(pipeline.initial + ['--launch-stagger', '0']) == 0
    assert [e[1] for e in pipeline.events if e[0] == 'render'] == [
        [['plane_grid', 5000.0, 0.0], ['plane_grid', 5000.0, 120.0]], [['plane_grid', 5000.0, 120.0]]]


def test_unreaped_worker_retires_its_slot(pipeline):
    """A UE still alive after SIGKILL holds GPU state: its slot must not launch again."""
    pipeline.behavior.render_rc = EXIT_WORKER_UNREAPED
    assert renderer.main(pipeline.initial + ['--shards-per-scene', '2', '--launch-stagger', '0']) == 1
    result = json.loads((pipeline.run / 'result.json').read_text())
    assert len([e for e in pipeline.events if e[0] == 'render']) == 1
    assert result['completed'] == 1 and result['results'][0]['teardown_incomplete']
    assert [r['slot'] for r in result['retired_slots']] == [0]
    assert len(result['unscheduled_jobs']) == 1 and not result['success']


def test_unreaped_worker_with_missing_outputs_is_requeued_not_lost(pipeline):
    pipeline.behavior.partial_fail = True
    pipeline.behavior.render_rc = EXIT_WORKER_UNREAPED
    assert renderer.main(pipeline.initial + ['--launch-stagger', '0']) == 1
    result = json.loads((pipeline.run / 'result.json').read_text())
    assert len(result['retired_slots']) == 1
    assert result['unscheduled_jobs'] == [json.loads((pipeline.run / 'plan.json').read_text())['jobs'][0]['id']]


def test_launches_are_paced_after_a_slow_failed_shared_probe(pipeline, monkeypatch):
    launches = []

    def slow_failed_probe(*_, **__):
        time.sleep(1.0)
        raise RuntimeError('probe timed out')

    class Child(pipeline.process_class):
        def __init__(self, command, **kwargs):
            if command[1].endswith('run_cmd_async.py'):
                launches.append((time.monotonic(), command))
            super().__init__(command, **kwargs)

    monkeypatch.setattr(gpu_devices, 'build_device_map', slow_failed_probe)
    monkeypatch.setattr(renderer.subprocess, 'Popen', Child)
    assert renderer.main(pipeline.initial + ['--gpu-ids', '0,1', '--shards-per-scene', '2',
                                             '--launch-stagger', '0.3']) == 0
    assert len(launches) == 2
    assert launches[1][0] - launches[0][0] >= 0.25
    for _, command in launches:
        assert '--device-map' not in command
        assert command[command.index('--probe-lock') + 1] == str(pipeline.run / 'vulkan_probe.lock')


def test_throughput_counts_images_rendered_by_earlier_attempts(pipeline, monkeypatch):
    """Partial render, then render + failed camera export, then camera only."""
    calls = {'render': 0, 'camera': 0}

    class Child(pipeline.process_class):
        def __init__(self, command, **kwargs):
            kind = 'render' if command[1].endswith('run_cmd_async.py') else 'camera'
            calls[kind] += 1
            pipeline.behavior.partial_fail = kind == 'render' and calls['render'] == 1
            pipeline.behavior.camera_fail = kind == 'camera' and calls['camera'] == 1
            super().__init__(command, **kwargs)

    monkeypatch.setattr(renderer.subprocess, 'Popen', Child)
    assert renderer.main(pipeline.initial + ['--launch-stagger', '0']) == 0
    assert calls == {'render': 2, 'camera': 2}
    result = json.loads((pipeline.run / 'result.json').read_text())
    job = result['results'][0]
    assert job['rendered_tasks'] == 2
    assert job['rendered_image_files'] == job['output']['image_files'] > 0
    assert result['image_files_per_second'] > 0


def test_retry_exhaustion_below_budget_finishes_other_jobs(pipeline, monkeypatch):
    class Child(pipeline.process_class):
        def __init__(self, command, **kwargs):
            if command[1].endswith('run_cmd_async.py'):
                worker = json.loads(Path(command[command.index('--worker-config') + 1]).read_text())
                if worker['trajectory_tasks'] == [['plane_grid', 5000.0, 120.0]]:
                    # This shard crashes before writing anything, every time.
                    pipeline.events.append(('render', worker['trajectory_tasks'], 'bad'))
                    self.returncode = 9
                    return
            super().__init__(command, **kwargs)

    monkeypatch.setattr(renderer.subprocess, 'Popen', Child)
    code = renderer.main(pipeline.initial + ['--shards-per-scene', '2', '--max-retries', '1',
                                             '--max-failures', '5', '--launch-stagger', '0'])
    result = json.loads((pipeline.run / 'result.json').read_text())
    assert code == 1 and not result['success']
    assert result['completed'] == 1 and result['failed_count'] == 1
    renders = [e for e in pipeline.events if e[0] == 'render']
    assert len(renders) == 3  # good shard once, bad shard twice; nothing was cancelled


def test_concurrent_retries_finish_every_job(pipeline, monkeypatch):
    """Requeued jobs must not be stranded when other worker threads have exited."""
    seen = set()
    lock = threading.Lock()

    class Child(pipeline.process_class):
        def __init__(self, command, **kwargs):
            if command[1].endswith('run_cmd_async.py'):
                worker = command[command.index('--worker-config') + 1]
                with lock:
                    first = worker not in seen
                    seen.add(worker)
                if first:
                    self.returncode = 5
                    return
            super().__init__(command, **kwargs)

    monkeypatch.setattr(renderer.subprocess, 'Popen', Child)
    assert renderer.main(pipeline.initial + ['--gpu-ids', '0,1', '--workers-per-gpu', '2',
                                             '--shards-per-scene', '2', '--launch-stagger', '0']) == 0
    result = json.loads((pipeline.run / 'result.json').read_text())
    assert result['completed'] == result['planned'] == 2 and result['failed_count'] == 0


def test_scheduler_writes_device_map_once_and_passes_it_to_workers(pipeline, monkeypatch):
    calls = []

    def fake_map(gpus, **_):
        calls.append([g['uuid'] for g in gpus])
        return {'schema': gpu_devices.DEVICE_MAP_SCHEMA, 'host': 'fixture', 'icd': '/fixture.json',
                'gpus': {g['uuid']: {'uuid': g['uuid'], 'vulkan_index': i} for i, g in enumerate(gpus)},
                'probe': {'timings': {}}}

    commands = []

    class Child(pipeline.process_class):
        def __init__(self, command, **kwargs):
            commands.append(command)
            super().__init__(command, **kwargs)

    monkeypatch.setattr(gpu_devices, 'build_device_map', fake_map)
    monkeypatch.setattr(renderer.subprocess, 'Popen', Child)
    assert renderer.main(pipeline.initial + ['--gpu-ids', '0,1', '--shards-per-scene', '2',
                                             '--launch-stagger', '0']) == 0
    assert calls == [['GPU-00000000', 'GPU-00000001']]
    renders = [c for c in commands if c[1].endswith('run_cmd_async.py')]
    assert len(renders) == 2
    for command in renders:
        assert command[command.index('--device-map') + 1] == str(pipeline.run / 'device_bindings.json')
        assert command[command.index('--gpu-uuid') + 1].startswith('GPU-')
    assert (pipeline.run / 'device_bindings.json').exists()


def test_resume_rejects_changed_seed_before_launching_workers(pipeline):
    assert renderer.main(pipeline.initial) == 0
    pipeline.events.clear()
    with pytest.raises(ValueError, match='differs'):
        renderer.main(pipeline.resume + ['--seed', '43'])
    assert pipeline.events == []


def test_legacy_mount_alias_fingerprint_survives_repeated_resumes(pipeline, tmp_path):
    assert renderer.main(pipeline.initial) == 0
    path = pipeline.run / 'plan.json'
    plan = json.loads(path.read_text())
    alias = tmp_path / 'project_alias'
    alias.symlink_to(tmp_path / 'project', target_is_directory=True)
    old_files = plan['dataset']['plan_files']
    plan['dataset']['plan_files'] = {str(alias / 'Config/Dataset/train_sequences.csv'): next(iter(old_files.values()))}
    identity = {'jobs': plan['jobs'], 'render': plan['render_config'], 'trajectory': plan['trajectory_config'],
                'seed': 42, 'sync_profile': 'balanced', 'dataset': plan['dataset']}
    plan['fingerprint'] = hashlib.sha256(json.dumps(identity, sort_keys=True).encode()).hexdigest()
    path.write_text(json.dumps(plan))
    for marker in pipeline.run.glob('*/done.json'):
        value = json.loads(marker.read_text())
        value['fingerprint'] = plan['fingerprint']
        marker.write_text(json.dumps(value))
    pipeline.events.clear()
    assert renderer.main(pipeline.resume) == renderer.main(pipeline.resume) == 0
    assert pipeline.events == []
    assert json.loads(path.read_text())['dataset']['plan_files'] == plan['dataset']['plan_files']


def relocate_project(pipeline, tmp_path, keep_original=False):
    original = tmp_path / 'project'
    relocated = tmp_path / 'relocated_project'
    if keep_original:
        shutil.copytree(original, relocated)
    else:
        original.rename(relocated)
    config_path = Path(pipeline.initial[pipeline.initial.index('--config') + 1])
    config = json.loads(config_path.read_text())
    config['ue_project'] = str(relocated / 'fixture.uproject')
    config_path.write_text(json.dumps(config))
    return relocated


@pytest.mark.parametrize('keep_original', [False, True])
def test_relocated_project_resumes_repeatedly_without_changing_recipe(pipeline, tmp_path, keep_original):
    assert renderer.main(pipeline.initial) == 0
    plan_path = pipeline.run / 'plan.json'
    original = json.loads(plan_path.read_text())
    relocate_project(pipeline, tmp_path, keep_original)
    pipeline.events.clear()
    assert renderer.main(pipeline.resume) == renderer.main(pipeline.resume) == 0
    assert pipeline.events == []
    current = json.loads(plan_path.read_text())
    assert current['fingerprint'] == original['fingerprint']
    assert current['dataset'] == original['dataset']
    assert current['total_workers'] == 2
    result = json.loads((pipeline.run / 'result.json').read_text())
    assert result['completed'] == result['resumed'] == 1


def test_relocated_project_rejects_changed_local_csv_even_if_old_copy_exists(pipeline, tmp_path):
    assert renderer.main(pipeline.initial) == 0
    original = (pipeline.run / 'plan.json').read_bytes()
    relocated = relocate_project(pipeline, tmp_path, keep_original=True)
    # The rows and selected jobs are identical; even a byte-only plan change
    # must fail its recorded content hash before any worker is launched.
    with (relocated / 'Config/Dataset/train_sequences.csv').open('a') as stream:
        stream.write('\n')
    pipeline.events.clear()
    with pytest.raises(ValueError, match='differs from the original image recipe'):
        renderer.main(pipeline.resume)
    assert pipeline.events == []
    assert (pipeline.run / 'plan.json').read_bytes() == original


def test_missing_csv_can_be_restored_with_explicit_unchanged_copy(pipeline, tmp_path):
    csv_path = tmp_path / 'project/Config/Dataset/train_sequences.csv'
    custom = tmp_path / 'custom_sequences.csv'
    csv_path.rename(custom)
    assert renderer.main(pipeline.initial + ['--sequence-plan', str(custom)]) == 0
    original = json.loads((pipeline.run / 'plan.json').read_text())
    moved = tmp_path / 'restored_sequences.csv'
    custom.rename(moved)
    pipeline.events.clear()
    with pytest.raises(FileNotFoundError, match='pass --sequence-plan'):
        renderer.main(pipeline.resume)
    assert pipeline.events == []
    resume = pipeline.resume + ['--sequence-plan', str(moved)]
    assert renderer.main(resume) == renderer.main(resume) == 0
    assert pipeline.events == []
    assert json.loads((pipeline.run / 'plan.json').read_text())['fingerprint'] == original['fingerprint']


def test_run_and_output_locks_reject_concurrent_scheduler(tmp_path):
    path = tmp_path / '.render.lock'
    with render_resume.exclusive_lock(path):
        with pytest.raises(RuntimeError, match='Another renderer'):
            with render_resume.exclusive_lock(path):
                pytest.fail('Lock was acquired twice')


def test_auto_selects_all_detected_cards_and_idle_mode_remains_explicit(monkeypatch):
    cards = [{'index': 0, 'uuid': 'GPU-00000000', 'memory_used_mb': 0, 'utilization_gpu_percent': 0},
             {'index': 3, 'uuid': 'GPU-33333333', 'memory_used_mb': 2000, 'utilization_gpu_percent': 30}]
    monkeypatch.setattr(gpu_devices, 'gpu_inventory', lambda: cards)
    assert renderer.choose_gpus('auto') == cards
    assert renderer.choose_gpus('idle') == cards[:1]


def test_auto_worker_budget_adapts_to_each_card():
    cards = [{'index': 0, 'memory_total_mb': 143771, 'memory_used_mb': 0},
             {'index': 1, 'memory_total_mb': 49152, 'memory_used_mb': 0}]
    counts = render_resume.worker_allocation(cards, 'auto')
    assert 1 <= counts[0] <= 12 and 1 <= counts[1] <= 4
    assert render_resume.worker_allocation(cards, '3') == [3, 3]


@pytest.mark.parametrize(('second_card_mb', 'second_workers'),
                         [(81559, 7), (80 * 1024, 8), (140 * 1024, 11)])
def test_fixed_100_gib_budget_caps_large_cards_and_accounts_for_existing_usage(second_card_mb, second_workers):
    cards = [{'index': 0, 'memory_total_mb': 80 * 1024, 'memory_used_mb': 0},
             {'index': 1, 'memory_total_mb': second_card_mb, 'memory_used_mb': 0}]
    assert render_resume.worker_allocation(cards, 'auto', target=0.9,
                                            vram_budget_mb=100 * 1024) == [8, second_workers]
    cards[0]['memory_used_mb'] = 8 * 1024
    assert render_resume.worker_allocation(cards, 'auto', target=0.9,
                                            vram_budget_mb=100 * 1024) == [7, second_workers]
    with pytest.raises(ValueError, match='exceeds the VRAM budget'):
        render_resume.worker_allocation(cards, '12', target=0.9, vram_budget_mb=100 * 1024)


def test_resume_can_change_vram_budget_without_changing_image_recipe(pipeline):
    assert renderer.main(pipeline.initial) == 0
    initial_plan = json.loads((pipeline.run / 'plan.json').read_text())
    assert initial_plan['vram_budget_gib_per_gpu'] == 100
    fingerprint = initial_plan['fingerprint']
    pipeline.events.clear()
    assert renderer.main(pipeline.resume + ['--vram-budget-gb', '63']) == 0
    plan = json.loads((pipeline.run / 'plan.json').read_text())
    assert plan['vram_budget_gib_per_gpu'] == 63 and plan['fingerprint'] == fingerprint
    assert pipeline.events == []


def test_session_resumes_existing_train_then_creates_test(pipeline, tmp_path, monkeypatch):
    session = tmp_path / 'session'
    (session / 'train').mkdir(parents=True)
    (session / 'train/plan.json').write_text('{}')
    calls = []

    class Child:
        pid = 12345

        def __init__(self, command, **_):
            calls.append(command)

        def poll(self):
            return 0

        def wait(self, timeout=None):
            return 0

    monkeypatch.setattr(forest.subprocess, 'Popen', Child)
    assert forest.main(['--config', pipeline.initial[1], '--run-dir', str(session),
                        '--output-root', str(tmp_path / 'dataset'), '--resume']) == 0
    assert len(calls) == 2 and '--resume' in calls[0] and '--resume' not in calls[1]
    assert [c[c.index('--split') + 1] for c in calls] == ['train', 'test']
    assert all(c[c.index('--gpu-ids') + 1] == 'auto' for c in calls)
    assert all(float(c[c.index('--vram-budget-gb') + 1]) == 100 for c in calls)
    assert json.loads((session / 'session.json').read_text())['status'] == 'complete'


def test_session_stops_before_test_when_train_fails(pipeline, tmp_path, monkeypatch):
    calls = []

    class Child:
        pid = 12345

        def __init__(self, command, **_):
            calls.append(command)

        def poll(self):
            return 7

        def wait(self, timeout=None):
            return 7

    monkeypatch.setattr(forest.subprocess, 'Popen', Child)
    session = tmp_path / 'session'
    assert forest.main(['--config', pipeline.initial[1], '--run-dir', str(session),
                        '--output-root', str(tmp_path / 'dataset')]) == 7
    assert len(calls) == 1
    assert json.loads((session / 'session.json').read_text())['status'] == 'failed'


def test_session_refuses_existing_data_without_original_checkpoint(pipeline, tmp_path):
    dataset = tmp_path / 'dataset/train'
    dataset.mkdir(parents=True)
    (dataset / 'existing-data.txt').write_text('preserve me')
    with pytest.raises(ValueError, match='no matching checkpoint'):
        forest.main(['--config', pipeline.initial[1], '--run-dir', str(tmp_path / 'new-session'),
                     '--output-root', str(dataset.parent), '--resume'])
    assert (dataset / 'existing-data.txt').read_text() == 'preserve me'


def test_low_disk_stops_new_launches_and_reports_unscheduled(pipeline, monkeypatch):
    usage = renderer.shutil.disk_usage
    monkeypatch.setattr(renderer.shutil, 'disk_usage',
                        lambda path: usage(path)._replace(free=1024 ** 3))
    assert renderer.main(pipeline.initial + ['--launch-stagger', '0']) == 1
    result = json.loads((pipeline.run / 'result.json').read_text())
    assert pipeline.events == [] and result['disk_low'] and len(result['unscheduled_jobs']) == 1
    pipeline.events.clear()
    monkeypatch.setattr(renderer.shutil, 'disk_usage', usage)
    assert renderer.main(pipeline.resume) == 0


def test_failed_run_wakes_launchers_waiting_in_the_pacer(pipeline, monkeypatch):
    class Child(pipeline.process_class):
        def __init__(self, command, **kwargs):
            super().__init__(command, **kwargs)
            if command[1].endswith('run_cmd_async.py'):
                self.returncode = 9

    monkeypatch.setattr(renderer.subprocess, 'Popen', Child)
    begin = time.monotonic()
    assert renderer.main(pipeline.initial + ['--gpu-ids', '0,1', '--shards-per-scene', '2',
                                             '--max-retries', '0', '--max-failures', '1',
                                             '--launch-stagger', '30']) == 1
    assert time.monotonic() - begin < 10
    assert len([e for e in pipeline.events if e[0] == 'render']) == 1
