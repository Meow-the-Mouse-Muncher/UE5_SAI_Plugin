"""Read the approved forest sequence plan without expanding its sampling budget."""
import csv
import hashlib
import json
from collections import Counter, defaultdict
from pathlib import Path


def file_sha(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for chunk in iter(lambda: stream.read(8 * 1024 * 1024), b''):
            h.update(chunk)
    return h.hexdigest()


def installed_manifest(project):
    root = Path(project).resolve().parent
    path = root / 'Config/Dataset/dataset_manifest.json'
    data = json.loads(path.read_text(encoding='utf-8'))
    if data.get('owner') != 'ForestDataset' or data.get('status') != 'final' or len(data.get('maps', [])) != 28:
        raise ValueError('The final 28-map forest manifest is required')
    files = json.loads((root / 'Config/Dataset/asset_manifest.json').read_text(encoding='utf-8'))
    for relative, expected in files['files'].items():
        source = (root / relative).resolve()
        if not source.is_relative_to(root) or not source.is_file() or file_sha(source) != expected['sha256']:
            raise ValueError('Missing or changed final dataset dependency: ' + relative)
    return data, {'manifest_sha256': file_sha(path),
                  'asset_manifest_sha256': file_sha(root / 'Config/Dataset/asset_manifest.json'),
                  'checked_files': len(files['files'])}


def jobs_from_csv(paths, manifest, maps, targets, shard_count, task_limit, trajectory_tasks):
    allowed = {tuple(t) for t in trajectory_tasks}
    records = {m['map']: m for m in manifest['maps']}
    grouped = defaultdict(list)
    ids, identities = set(), set()
    selected = []
    for path in paths:
        with Path(path).open(encoding='utf-8-sig', newline='') as stream:
            rows = list(csv.DictReader(stream))
        if not rows:
            raise ValueError('Empty sequence plan: ' + str(path))
        for raw in rows:
            row = dict(raw)
            package = row['map_path']
            record = records.get(package)
            if record is None or row['split'] != record['split'] or row['source_scene'] != record['source_scene']:
                raise ValueError('Sequence plan source/split does not match final maps')
            if row['target_label'] not in record['targets'] or int(row['expected_views']) != 25:
                raise ValueError('Unknown complete Target or non25-view sequence')
            task = (row['trajectory'], float(row['height_m']) * 100, float(row['yaw_deg']))
            identity = (package, row['target_label'], task)
            if task not in allowed or row['sequence_id'] in ids or identity in identities:
                raise ValueError('Duplicate or unsupported sequence recipe')
            ids.add(row['sequence_id']); identities.add(identity)
            if package in maps and (not targets or row['target_label'] in targets):
                row['task'] = list(task)
                grouped[(package, row['target_label'])].append(row)
    if not grouped:
        raise ValueError('No planned sequences match the requested split/maps/Targets')
    if targets and set(targets) - {t for _, t in grouped}:
        raise ValueError('Requested Target is absent from the selected final maps')
    jobs = []
    for (package, target), rows in sorted(grouped.items()):
        if task_limit:
            rows = rows[:task_limit]
        for index in range(min(shard_count, len(rows))):
            part = rows[index::shard_count]
            selected.extend(part)
            record = records[package]
            jobs.append({'id': '{}_{}_s{:03d}'.format(package.rsplit('/', 1)[-1], target, index),
                         'map_package_path': package, 'target_names': [target],
                         'trajectory_tasks': [r['task'] for r in part],
                         'sequence_ids': [r['sequence_id'] for r in part],
                         'split': record['split'],
                         'occlusion_actor_names': record['occlusion_actor_names']})
    summary = {'selected_sequences': len(selected), 'jobs': len(jobs),
               'split_counts': dict(Counter(r['split'] for r in selected)),
               'map_counts': dict(Counter(r['map_path'] for r in selected)),
               'plan_files': {str(p): file_sha(p) for p in paths},
               'complete_views_per_sequence': 25}
    return jobs, summary, selected


def output_index(rows, path):
    if not rows:
        return
    columns = [k for k in rows[0] if k != 'task'] + ['occ_directory', 'gt_directory']
    with Path(path).open('w', encoding='utf-8', newline='') as stream:
        writer = csv.DictWriter(stream, columns)
        writer.writeheader()
        for raw in rows:
            row = {k: v for k, v in raw.items() if k != 'task'}
            name = '{}_{}_height_{:03d}_ang_{:03d}'.format(row['map_path'].rsplit('/', 1)[-1],
                row['target_label'], int(float(row['height_m'])), int(float(row['yaw_deg'])))
            row.update(occ_directory=row['trajectory'] + '/' + name + '_occ',
                       gt_directory=row['trajectory'] + '/' + name + '_GT')
            writer.writerow(row)


def check_output(root, job, render):
    """Require every planned frame/pass, including on resume."""
    root = Path(root)
    single = bool(render.get('Single_Frame_Only', False))
    expected = {int(render.get('Single_Frame_Index', 0))} if single else set(range(25))
    suffixes = ['occ'] if render.get('Only_OCC', False) else ['occ', 'GT']
    passes = [p for p in render['Render_Passes'] if p.get('enable')]
    for kind, height, angle in job['trajectory_tasks']:
        for target in job['target_names']:
            prefix = '{}_{}_height_{:03d}_ang_{:03d}'.format(job['map_package_path'].rsplit('/', 1)[-1],
                                                          target, int(height / 100), int(angle))
            for suffix in suffixes:
                for p in passes:
                    folder = root / kind / (prefix + '_' + suffix) / p['pass_name']
                    files = list(folder.glob('*.' + p['ext']))
                    try:
                        frames = {int(f.stem) for f in files}
                    except ValueError:
                        raise ValueError('Unexpected frame names: ' + str(folder))
                    if frames != expected or len(files) != len(expected) or any(f.stat().st_size == 0 for f in files):
                        raise ValueError('Incomplete planned render frames: ' + str(folder))

