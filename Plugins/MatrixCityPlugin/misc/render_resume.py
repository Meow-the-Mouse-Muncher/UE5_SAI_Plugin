"""Durable render stages and exact sequence identities (no Unreal dependency)."""
import contextlib
import copy
import hashlib
import json
import os
from pathlib import Path


def camera_jobs(root, job):
    root = Path(root).resolve()
    result = []
    for kind, height, angle in job['trajectory_tasks']:
        for target in job.get('target_names') or []:
            prefix = '{}_{}_height_{:03d}_ang_{:03d}'.format(
                job['map_package_path'].rsplit('/', 1)[-1], target, int(height / 100), int(angle))
            result.append({'output_root': str(root), 'trajectory': kind, 'prefix': prefix,
                           'fbx_path': str(root / 'Exported_FBX' / kind / (prefix + '.fbx')),
                           'GT_dir': str(root / kind / (prefix + '_GT')),
                           'occ_dir': str(root / kind / (prefix + '_occ'))})
    return result


def camera_pair_valid(job):
    from camera_json_protocol import _validate_payload, _sha256
    try:
        paths = [Path(job[key]) / 'pose/transforms.json' for key in ('GT_dir', 'occ_dir')]
        content = paths[0].read_bytes()
        if content != paths[1].read_bytes():
            return False
        payload = json.loads(content)
        _validate_payload(payload)
        return (payload.get('trajectory') == job['trajectory']
                and payload.get('sequence_prefix') == job['prefix']
                and payload.get('source', {}).get('fbx_sha256') == _sha256(Path(job['fbx_path'])))
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        return False


def remaining_render_job(root, job, render):
    """Reuse only complete frame sets with a nonempty source FBX."""
    import dataset_plan
    result = copy.deepcopy(job)
    result['trajectory_tasks'] = []
    if 'sequence_ids' in result:
        result['sequence_ids'] = []
    for index, task in enumerate(job['trajectory_tasks']):
        part = dict(job, trajectory_tasks=[task])
        try:
            dataset_plan.check_output(root, part, render)
            for camera in camera_jobs(root, part):
                if Path(camera['fbx_path']).stat().st_size == 0:
                    raise ValueError('Empty source FBX')
        except (OSError, ValueError):
            result['trajectory_tasks'].append(task)
            if 'sequence_ids' in result:
                result['sequence_ids'].append(job['sequence_ids'][index])
    return result


def resume_sequence_plans(plan, project):
    """Locate approved CSVs in the current project after a deployment move."""
    paths = []
    for recorded in (plan.get('dataset') or {}).get('plan_files', {}):
        path = Path(recorded).expanduser()
        if (path.parent.parts[-2:] == ('Config', 'Dataset')
                and path.name in ('train_sequences.csv', 'test_sequences.csv')):
            local = Path(project).parent / 'Config/Dataset' / path.name
            if local.is_file():
                path = local
        if not path.is_file():
            raise FileNotFoundError(
                'Cannot locate recorded sequence plan {}; restore the file or pass '
                '--sequence-plan with an unchanged copy'.format(recorded))
        paths.append(path.resolve())
    return paths


def compatible_resume(old, candidate, seed, sync_profile):
    """Accept relocated CSVs and GPU changes; reject changes to image recipes."""
    identity = {'jobs': old['jobs'], 'render': old['render_config'],
                'trajectory': old['trajectory_config'], 'seed': seed,
                'sync_profile': sync_profile, 'dataset': old.get('dataset')}
    digest = hashlib.sha256(json.dumps(identity, sort_keys=True).encode()).hexdigest()
    if digest != old['fingerprint']:
        return False

    def normalized(plan):
        data = copy.deepcopy({key: plan.get(key) for key in
                              ('jobs', 'render_config', 'trajectory_config', 'dataset')})
        data['render_config']['Output_Path'] = str(Path(data['render_config']['Output_Path']).resolve())
        if data['dataset']:
            # CSV locations are deployment details. Keep every content hash
            # (including multiplicity) so a move cannot change the approved plan.
            data['dataset']['plan_files'] = sorted(data['dataset'].get('plan_files', {}).values())
        return data
    return normalized(old) == normalized(candidate)


@contextlib.contextmanager
def exclusive_lock(path):
    import fcntl
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('a+') as stream:
        try:
            fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise RuntimeError('Another renderer owns ' + str(path)) from exc
        stream.seek(0)
        stream.truncate()
        stream.write(str(os.getpid()) + '\n')
        stream.flush()
        try:
            yield
        finally:
            fcntl.flock(stream, fcntl.LOCK_UN)


def worker_allocation(gpus, requested, memory_budget_mb=9216, target=0.8, vram_budget_mb=None):
    """Budget VRAM per card, then cap automatic allocation by remaining host RAM."""
    if requested != 'auto':
        count = int(requested)
        if count < 1:
            raise ValueError('--workers-per-gpu must be auto or a positive integer')
        if vram_budget_mb is not None:
            for gpu in gpus:
                total = gpu.get('memory_total_mb')
                ceiling = min(vram_budget_mb, total * target) if total is not None else vram_budget_mb
                if count * memory_budget_mb + (gpu.get('memory_used_mb') or 0) > ceiling:
                    raise ValueError('Explicit worker count exceeds the VRAM budget on GPU {}'.format(gpu['index']))
        return [count] * len(gpus)
    counts = []
    for gpu in gpus:
        total = gpu.get('memory_total_mb')
        used = gpu.get('memory_used_mb') or 0
        if total is None:  # Explicit/offline --dry-run has no hardware inventory.
            counts.append(1)
            continue
        ceiling = total * target
        if vram_budget_mb is not None:
            ceiling = min(ceiling, vram_budget_mb)
        budget = ceiling - used
        if budget < memory_budget_mb:
            raise ValueError('Insufficient free VRAM on GPU {} for one worker'.format(gpu['index']))
        counts.append(max(1, int(budget // memory_budget_mb)))
    available = None
    try:
        for line in Path('/proc/meminfo').read_text().splitlines():
            if line.startswith('MemAvailable:'):
                available = int(line.split()[1]) * 1024
        limit = Path('/sys/fs/cgroup/memory.max').read_text().strip()
        if limit != 'max':
            remaining = int(limit) - int(Path('/sys/fs/cgroup/memory.current').read_text())
            available = min(available, remaining) if available is not None else remaining
    except (OSError, ValueError):
        pass
    if available is not None:
        maximum = int(available * 0.8 / (6144 * 1024**2))
        if maximum < len(gpus):
            raise ValueError('Insufficient host RAM for one worker per selected GPU')
        while sum(counts) > maximum:
            index = max(range(len(counts)), key=lambda i: counts[i])
            counts[index] -= 1
    return counts
