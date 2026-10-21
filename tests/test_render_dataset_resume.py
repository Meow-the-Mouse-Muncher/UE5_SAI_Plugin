"""Exercise durable stage recovery and scheduling without launching Unreal."""
import csv
import hashlib
import json
from pathlib import Path
import sys
import threading
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
    behavior = SimpleNamespace(camera_fail=False, partial_fail=False)

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
    assert renderer.main(pipeline.initial) == 1
    pipeline.behavior.partial_fail = False
    pipeline.events.clear()
    assert renderer.main(pipeline.resume) == 0
    assert [e[1] for e in pipeline.events if e[0] == 'render'] == [[['plane_grid', 5000.0, 120.0]]]
    assert [e for e in pipeline.events if e[0] == 'camera'] == [('camera', 2)]


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


def test_fixed_70_gib_budget_caps_large_cards_and_accounts_for_existing_usage():
    cards = [{'index': 0, 'memory_total_mb': 80 * 1024, 'memory_used_mb': 0},
             {'index': 1, 'memory_total_mb': 140 * 1024, 'memory_used_mb': 0}]
    assert render_resume.worker_allocation(cards, 'auto', target=0.9,
                                            vram_budget_mb=70 * 1024) == [7, 7]
    cards[0]['memory_used_mb'] = 8 * 1024
    assert render_resume.worker_allocation(cards, 'auto', target=0.9,
                                            vram_budget_mb=70 * 1024) == [6, 7]
    with pytest.raises(ValueError, match='exceeds the VRAM budget'):
        render_resume.worker_allocation(cards, '8', target=0.9, vram_budget_mb=70 * 1024)


def test_resume_can_change_vram_budget_without_changing_image_recipe(pipeline):
    assert renderer.main(pipeline.initial) == 0
    fingerprint = json.loads((pipeline.run / 'plan.json').read_text())['fingerprint']
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
    assert all(float(c[c.index('--vram-budget-gb') + 1]) == 70 for c in calls)
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
