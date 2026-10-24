#!/usr/bin/env python3
"""Export camera JSON from an existing 25-view/512 render run, without rendering."""
import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

if __name__ == '__main__':
    sys.dont_write_bytecode = True

SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))
from camera_json_protocol import discover_jobs, load_jobs_manifest


def resolve_run(output_root=None, run_dir=None):
    if bool(output_root) == bool(run_dir):
        raise ValueError('Select exactly one of --output-root or --run-dir')
    if output_root:
        root = Path(output_root).expanduser().resolve(strict=True)
        return root, root/'metadata/camera_export_report.json'
    run = Path(run_dir).expanduser().resolve(strict=True)
    with (run/'plan.json').open(encoding='utf-8') as f:
        plan = json.load(f)
    if plan.get('render_config', {}).get('Resolution') != [512, 512]:
        raise ValueError('Render plan must declare native Resolution [512,512]')
    if plan.get('trajectory_config', {}).get('global', {}).get('num_frames') != 25:
        raise ValueError('Render plan must declare exactly 25 input frames')
    root = Path(plan['output_root']).expanduser()
    if not root.is_absolute():
        raise ValueError('Render plan output_root must be absolute')
    return root.resolve(strict=True), run/'camera_export_report.json'


def blender_binary(explicit=None, *, required=True):
    selected = explicit or os.environ.get('BLENDER_BIN')
    if selected:
        path = Path(selected).expanduser()
        if not path.is_file() or not os.access(path, os.X_OK):
            raise FileNotFoundError(f'Blender executable is unavailable: {path}')
        return str(path.resolve())
    # A task-local runtime can live beside this repository's code directory.
    tools = SCRIPT_DIR.parents[2]/'tools/blender'
    candidates = sorted(path for path in tools.glob('*/blender') if path.is_file() and os.access(path, os.X_OK))
    if len(candidates) == 1:
        return str(candidates[0].resolve())
    if len(candidates) > 1:
        raise ValueError('Multiple Blender runtimes exist; select --blender explicitly')
    available = shutil.which('blender')
    if available:
        return str(Path(available).resolve())
    if not required:
        return 'blender'
    raise FileNotFoundError('No Blender executable found; use --blender or BLENDER_BIN')


def parser():
    p = argparse.ArgumentParser(description=__doc__)
    group = p.add_mutually_exclusive_group(required=True)
    group.add_argument('--output-root')
    group.add_argument('--run-dir')
    p.add_argument('--fbx-root')
    p.add_argument('--trajectory', action='append')
    p.add_argument('--sequence', action='append')
    p.add_argument('--limit', type=int)
    p.add_argument('--camera-name', default='CineCameraActor1')
    p.add_argument('--blender')
    p.add_argument('--report')
    p.add_argument('--jobs-file', help='Exact sequence manifest supplied by the render scheduler')
    p.add_argument('--overwrite', action='store_true')
    p.add_argument('--dry-run', action='store_true', help='Validate paths/frames and print the plan without Blender or writes')
    return p


def main(argv=None):
    args = parser().parse_args(argv)
    root, default_report = resolve_run(args.output_root, args.run_dir)
    if args.jobs_file and any((args.fbx_root, args.trajectory, args.sequence, args.limit)):
        raise ValueError('--jobs-file cannot be combined with discovery filters')
    jobs = (load_jobs_manifest(args.jobs_file, root) if args.jobs_file else
            discover_jobs(root, args.fbx_root, args.trajectory, args.sequence, args.limit))
    binary = blender_binary(args.blender, required=not args.dry_run)
    command = [binary, '--background', '--factory-startup', '--disable-autoexec',
               '--threads', '2', '--python-exit-code', '2', '--python',
               str(SCRIPT_DIR/'blender_extract_transforms.py'), '--',
               '--output-root', str(root), '--camera-name', args.camera_name,
               '--report', str(Path(args.report).expanduser().resolve() if args.report else default_report)]
    for flag, values in (('--trajectory', args.trajectory), ('--sequence', args.sequence)):
        for value in values or []:
            command.extend([flag, value])
    if args.fbx_root:
        command.extend(['--fbx-root', str(Path(args.fbx_root).expanduser().resolve())])
    if args.jobs_file:
        command.extend(['--jobs-file', str(Path(args.jobs_file).expanduser().resolve())])
    if args.limit is not None:
        command.extend(['--limit', str(args.limit)])
    if args.overwrite:
        command.append('--overwrite')
    if args.dry_run:
        print(json.dumps({'dry_run': True, 'jobs': jobs, 'command': command}, ensure_ascii=False, indent=2))
        return 0
    clean = dict(os.environ)
    # Use Blender's bundled Python; do not inherit another project's modules.
    clean.pop('PYTHONPATH', None)
    clean.pop('PYTHONHOME', None)
    return subprocess.run(command, cwd=SCRIPT_DIR.parent, env=clean).returncode


if __name__ == '__main__':
    try:
        sys.exit(main())
    except (ValueError, KeyError, OSError, RuntimeError) as error:
        print(f'Camera export error: {error}', file=sys.stderr)
        sys.exit(2)
