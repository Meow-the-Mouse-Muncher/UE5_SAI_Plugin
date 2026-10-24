#!/usr/bin/env python3
"""Render train then test, with automatic GPUs, camera JSON and durable resume."""
import argparse
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import time

if __name__ == '__main__':
    sys.dont_write_bytecode = True

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'Plugins/MatrixCityPlugin/misc'))
from pipeline_config import dump_json, load_render_config, resolve_path
from render_dataset import configure_runtime
from render_resume import exclusive_lock


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--config', default=str(ROOT / 'Plugins/MatrixCityPlugin/misc/user.json'))
    p.add_argument('--run-dir', required=True, help='Session directory containing train/ and test/ plans')
    p.add_argument('--output-root', help='Dataset root containing train/ and test/ outputs')
    p.add_argument('--resume', action='store_true')
    p.add_argument('--gpu-ids', default='auto')
    p.add_argument('--workers-per-gpu', default='auto')
    p.add_argument('--worker-vram-mb', type=int, default=9216)
    p.add_argument('--vram-target', type=float, default=0.9)
    p.add_argument('--vram-budget-gb', type=float, default=100, help='Total estimated VRAM budget per GPU in GiB')
    p.add_argument('--camera-export-workers', default='auto', help='Concurrent Blender exporters; auto = CPU cores / 2')
    p.add_argument('--blender')
    p.add_argument('--timeout', type=float, default=0, help='Worker timeout in seconds; 0 disables it')
    p.add_argument('--dry-run', action='store_true')
    args = p.parse_args(argv)
    if (not 0 < args.vram_budget_gb < float('inf') or not 0 < args.vram_target <= 1
            or args.worker_vram_mb < 1 or args.timeout < 0
            or (args.camera_export_workers != 'auto'
                and (not args.camera_export_workers.isdigit() or int(args.camera_export_workers) < 1))):
        p.error('Invalid memory budget, exporter count or timeout')
    config_path = resolve_path(args.config)
    config = json.loads(config_path.read_text())
    configure_runtime(config, argv, entry_point=__file__, config_dir=config_path.parent)
    run = Path(args.run_dir).expanduser().resolve()
    previous_path = run / 'session.json'
    previous = json.loads(previous_path.read_text()) if previous_path.exists() else {}
    render = load_render_config(resolve_path(config['render_config'], config_path.parent))
    root = Path(args.output_root or (previous.get('output_root') if args.resume else None)
                or render['Output_Path']).expanduser()
    if not root.is_absolute():
        root = resolve_path(config['ue_project'], config_path.parent).parent / root
    root = root.resolve()
    print('Dataset: {}\nRun state and logs: {}\nVRAM allocation budget: {} GiB/GPU'.format(
        root, run, args.vram_budget_gb), file=sys.stderr, flush=True)
    commands = {}
    for split in ('train', 'test'):
        child_run = run / split
        command = [sys.executable, str(ROOT / 'Plugins/MatrixCityPlugin/misc/render_dataset.py'),
                   '--config', str(config_path), '--split', split, '--run-dir', str(child_run),
                   '--output-root', str(root / split), '--gpu-ids', args.gpu_ids,
                   '--workers-per-gpu', args.workers_per_gpu,
                   '--worker-vram-mb', str(args.worker_vram_mb), '--vram-target', str(args.vram_target),
                   '--vram-budget-gb', str(args.vram_budget_gb),
                   '--camera-export-workers', str(args.camera_export_workers), '--timeout', str(args.timeout)]
        if args.resume and (child_run / 'plan.json').is_file():
            command.append('--resume')
        else:
            folder = root / split
            if args.resume and folder.is_dir() and any(folder.iterdir()):
                raise ValueError('Existing data has no matching checkpoint at {}; restore the original run directory before resuming'.format(child_run))
            command.extend(['--sync-profile', 'balanced', '--shards-per-scene', '3'])
        if args.blender:
            command.extend(['--blender', args.blender])
        if args.dry_run:
            command.append('--dry-run')
        commands[split] = command
    if args.dry_run:
        for command in commands.values():
            result = subprocess.run(command, check=False)
            if result.returncode:
                return result.returncode
        return 0
    if not args.resume and (previous or any((run / s / 'plan.json').exists() for s in commands)):
        raise ValueError('Session already exists; use --resume')
    if not args.resume:
        for split in commands:
            folder = root / split
            if folder.exists() and any(folder.iterdir()):
                raise ValueError('Output already contains data; resume its existing session: ' + str(folder))
    run.mkdir(parents=True, exist_ok=True)
    root.mkdir(parents=True, exist_ok=True)
    stop = None
    process = None

    def request_stop(signum, _frame):
        nonlocal stop
        if stop is None:
            stop = signum

    state = {'schema': 'forest-render-session-v2', 'status': 'starting', 'output_root': str(root),
             'resume': args.resume, 'launcher_pid': os.getpid(), 'commands': commands,
             'started_unix': time.time(), 'runs': {}}

    def save():
        state['updated_unix'] = time.time()
        dump_json(previous_path, state)

    handlers = {s: signal.getsignal(s) for s in (signal.SIGINT, signal.SIGTERM)}
    with exclusive_lock(root / '.pipeline.lock'), exclusive_lock(run / '.pipeline.lock'):
        if previous and previous.get('schema') != state['schema']:
            backup = run / 'session.before_resume.json'
            if not backup.exists():
                dump_json(backup, previous)
        for sig in handlers:
            signal.signal(sig, request_stop)
        try:
            save()
            for split, command in commands.items():
                if stop is not None:
                    break
                state.update(status='running', active_split=split)
                with (run / (split + '_launcher.log')).open('ab') as log:
                    process = subprocess.Popen(command, stdout=log, stderr=log)
                    state['runs'][split] = {'pid': process.pid, 'started_unix': time.time()}
                    save()
                    stop_sent = False
                    while process.poll() is None:
                        if stop is not None and not stop_sent:
                            state['status'] = 'stopping'
                            save()
                            process.send_signal(signal.SIGINT)
                            stop_sent = True
                        time.sleep(0.2)
                    code = process.wait()
                state['runs'][split].update(returncode=code, finished_unix=time.time())
                save()
                if code or stop is not None:
                    state['status'] = 'stopped' if stop is not None else 'failed'
                    save()
                    return 128 + stop if stop is not None else code
            state['status'] = 'stopped' if stop is not None else 'complete'
            state['active_split'] = None
            save()
            return 128 + stop if stop is not None else 0
        finally:
            # An unexpected controller exception still leaves child teardown owned
            # by the scheduler, which gives UE its cooperative cancellation window.
            if process is not None and process.poll() is None:
                process.send_signal(signal.SIGINT)
                process.wait(timeout=120)
            for sig, handler in handlers.items():
                signal.signal(sig, handler)


if __name__ == '__main__':
    raise SystemExit(main())
