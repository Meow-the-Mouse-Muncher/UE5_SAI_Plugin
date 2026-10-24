"""Dynamically schedule disjoint scene/trajectory shards on selected physical GPUs."""
import argparse
import concurrent.futures
from contextlib import ExitStack
import hashlib
import json
import logging
import os
import queue
import shutil
import signal
import socket
import subprocess
import sys
import threading
import time
import uuid
from pathlib import Path

if __name__ == '__main__':
    sys.dont_write_bytecode = True

import yaml

from pipeline_config import (PLUGIN_ROOT, dump_json, load_render_config, partition_tasks,
                             resolve_map, resolve_path, trajectory_tasks)
from render_resume import (camera_jobs, camera_pair_valid, compatible_resume, exclusive_lock,
                           remaining_render_job, resume_sequence_plans, worker_allocation)
from run_cmd_async import EXIT_WORKER_UNREAPED, SUPERVISOR_STATUSES

SCRIPT_DIR = PLUGIN_ROOT.parents[1] / 'scripts'
sys.path.insert(0, str(SCRIPT_DIR))

# Supervisor stop worst case: 45s MRQ cancellation, 15s after SIGTERM and 30s
# for a SIGKILL that waits on an uninterruptible driver call, plus slack.
WORKER_STOP_SECONDS = 100
# export_camera_json.py runs each Blender exporter with --threads 2.
BLENDER_THREADS = 2


SYNC_PROFILES = {
    "legacy": {},
    "balanced": {"queue_wait_cycles": 1, "queue_wait_seconds": 0.25,
                 "step_wait_seconds": 0.1, "step_collect_garbage": False,
                 "map_wait_cycles": 2, "map_wait_seconds": 0.5,
                 "exit_wait_cycles": 1, "exit_wait_seconds": 0.5},
}


def configure_runtime(config, argv, *, entry_point=None, config_dir=None):
    """Select the configured Linux user before applying its runtime environment."""
    user = config.get("run_as_user")
    if user is not None:
        if not isinstance(user, str) or not user:
            raise ValueError("run_as_user must be a nonempty username")
        if sys.platform != "linux":
            raise ValueError("run_as_user is supported only on Linux")
        import pwd
        try:
            target_uid = pwd.getpwnam(user).pw_uid
        except KeyError as exc:
            raise ValueError("Configured runtime user does not exist: " + user) from exc
        uid = os.geteuid()
        if uid != target_uid:
            if uid != 0:
                raise PermissionError("Run this renderer as root or as " + user)
            return os.execvp("runuser", ["runuser", "-u", user, "--", sys.executable,
                                        str(Path(entry_point or __file__).resolve()), *argv])
    environment = config.get("environment", {})
    unset = config.get("unset_environment", [])
    if not isinstance(environment, dict) or not all(
            isinstance(key, str) and isinstance(value, str) for key, value in environment.items()):
        raise ValueError("environment must be a dictionary of strings")
    if not isinstance(unset, list) or not all(isinstance(key, str) for key in unset):
        raise ValueError("unset_environment must be a list of strings")
    for key in unset:
        os.environ.pop(key, None)
    os.environ.update(environment)
    if config.get('runtime_root') or os.environ.get('FOREST_RUNTIME_ROOT'):
        from render_storage import prepare_project_storage, resolve_runtime_root
        project = resolve_path(config['ue_project'], config_dir)
        runtime_root = resolve_runtime_root(project, config)
        dry_run = '--dry-run' in argv
        paths = prepare_project_storage(project, runtime_root, dry_run=dry_run)
        os.environ.update({
            'FOREST_RUNTIME_ROOT': str(runtime_root),
            'TMPDIR': str(paths['tmp']),
            'TMP': str(paths['tmp']),
            'TEMP': str(paths['tmp']),
            'XDG_CACHE_HOME': str(paths['cache']),
            'UE_LocalDataCachePath': str(paths['DerivedDataCache'] / 'Local'),
            'PYTHONDONTWRITEBYTECODE': '1',
        })
        sys.dont_write_bytecode = True
        if not dry_run:
            import tempfile
            # tempfile caches its selected directory independently of os.environ.
            tempfile.tempdir = str(paths['tmp'])
            (paths['DerivedDataCache'] / 'Local').mkdir(parents=True, exist_ok=True)


def choose_gpus(requested):
    from gpu_devices import find_gpu, gpu_inventory
    inventory = gpu_inventory()
    if requested == "auto":
        choices = inventory
    elif requested == "idle":
        choices = [g for g in inventory if g["memory_used_mb"] is not None
                   and g["utilization_gpu_percent"] is not None
                   and g["memory_used_mb"] < 1024 and g["utilization_gpu_percent"] < 10]
        if not choices:
            raise ValueError("No idle GPU found; specify --gpu-ids only when you intend to share a GPU")
    else:
        choices = [find_gpu(value.strip(), inventory) for value in requested.split(",")]
    if len({g["uuid"] for g in choices}) != len(choices):
        raise ValueError("GPU selection contains duplicates")
    return choices


def get_maps(config, project, requested=None):
    names = requested or config.get("maps")
    if not names:
        names = sorted({p.stem for p in (Path(project).parent / "Content/Map").rglob("scene_*.umap")})
    maps = [resolve_map(project, name) for name in names]
    if len(set(maps)) != len(maps):
        raise ValueError("Map selection contains duplicate packages")
    # Canonical output names contain map names, not their folders.
    if len({p.rsplit("/", 1)[-1] for p in maps}) != len(maps):
        raise ValueError("Map basenames collide in output filenames")
    return maps


def prepare_plan(config, trajectory, maps, shard_count, task_limit=None, targets=None):
    tasks = trajectory_tasks(trajectory)
    if task_limit:
        tasks = tasks[:task_limit]
    jobs = []
    for package in maps:
        for index, part in enumerate(partition_tasks(tasks, shard_count)):
            jobs.append({"id": package.rsplit("/", 1)[-1] + "_s{:03d}".format(index),
                         "map_package_path": package, "trajectory_tasks": part,
                         "target_names": targets})
    return jobs


def allocate_port(allocated_ports, lock):
    """Choose an OS-assigned port not previously issued by this scheduler.

    The lock protects both allocation and the lifetime history shared by workers.
    Closing the socket still leaves an external-process race before child bind.
    """
    with lock:
        while True:
            with socket.socket() as sock:
                sock.bind(("127.0.0.1", 0))
                port = sock.getsockname()[1]
            if port not in allocated_ports:
                allocated_ports.add(port)
                return port


def output_inventory(root, job):
    """Account only for this task's disjoint canonical output prefixes."""
    root = Path(root)
    total = size = frames = 0
    prefixes = []
    map_name = job["map_package_path"].rsplit("/", 1)[-1]
    for kind, height, angle in job["trajectory_tasks"]:
        folder = root / kind
        if job.get('target_names'):
            directories = [folder / ('{}_{}_height_{:03d}_ang_{:03d}_{}'.format(
                map_name, target, int(height / 100), int(angle), suffix))
                for target in job['target_names'] for suffix in ('GT', 'occ')]
        else:
            directories = folder.glob(map_name + "_Target_*_height_{:03d}_ang_{:03d}_*".format(int(height / 100), int(angle)))
        for directory in directories:
            if not directory.is_dir():
                continue
            stems = set()
            for path in directory.rglob("*"):
                if path.suffix.lower() in (".png", ".exr", ".jpg", ".jpeg") and path.is_file():
                    total += 1
                    size += path.stat().st_size
                    stems.add(path.stem)
            frames += len(stems)
            prefixes.append(directory.name)
    return {"image_files": total, "bytes": size, "frame_views": frames, "directories": sorted(prefixes)}


def _main(argv, resources):
    argv = list(sys.argv[1:] if argv is None else argv)
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", "-c", default=str(PLUGIN_ROOT / "misc/user.json"))
    parser.add_argument("--gpu-ids", "--gpu_ids", default="auto", help="auto: all detected GPUs; idle: only idle GPUs; or physical IDs/UUIDs")
    parser.add_argument("--workers-per-gpu", "--processes_per_gpu", default="auto", help="auto budgets VRAM and RAM; or an explicit positive integer")
    parser.add_argument("--worker-vram-mb", type=int, default=9216, help="Estimated peak MiB per UE worker for automatic allocation")
    parser.add_argument("--vram-target", type=float, default=0.9, help="Maximum fraction of physical VRAM to budget")
    parser.add_argument("--vram-budget-gb", type=float, default=100, help="Per-card total VRAM budget in GiB; allocation estimate, not a driver-enforced limit")
    parser.add_argument("--num_gpus", "--num-gpus", type=int, default=None, help="Limit the number of selected GPUs")
    parser.add_argument("--maps", "-m", nargs="+")
    parser.add_argument("--split", choices=("train", "test", "all"), default="train")
    parser.add_argument("--sequence-plan", help="Approved CSV; defaults to Config/Dataset for the selected split")
    parser.add_argument("--targets", nargs="+")
    parser.add_argument("--trajectory-config", default=str(PLUGIN_ROOT / "misc/trajectory_config.yaml"))
    parser.add_argument("--render-config")
    parser.add_argument("--output-root")
    parser.add_argument("--run-dir")
    parser.add_argument("--shards-per-scene", type=int)
    parser.add_argument("--task-limit", type=int, help="Explicit small benchmark subset of the trajectory product")
    parser.add_argument("--sync-profile", choices=sorted(SYNC_PROFILES), default="legacy")
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--timeout", type=float, default=0, help="Per-UE-worker timeout; zero means unlimited")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--skip-camera-json", action="store_true", help="Disable automatic 25-view camera metadata (e.g. nonstandard benchmarks)")
    parser.add_argument("--blender", help="Blender executable for automatic camera export")
    parser.add_argument("--camera-export-workers", default="auto",
                        help="Maximum concurrent Blender camera exporters; auto = CPU cores / {}".format(BLENDER_THREADS))
    parser.add_argument("--keep-going", action="store_true")
    parser.add_argument("--max-retries", type=int, default=2,
                        help="Requeue a failed job this many times; 0 disables retries")
    parser.add_argument("--max-failures", type=int, default=None,
                        help="Abort after this many jobs exhaust retries (default: 1%% of jobs, at least 3)")
    parser.add_argument("--launch-stagger", type=float, default=0.25,
                        help="Seconds between consecutive worker launches; spreads Vulkan startup load")
    parser.add_argument("--min-free-gb", type=float, default=20,
                        help="Stop launching new jobs when the run or output filesystem has less free space")
    parser.add_argument("--probe-timeout", type=float, default=30,
                        help="Seconds for the one-per-run Vulkan adapter probe")
    parser.add_argument("--probe-attempts", type=int, default=3,
                        help="Vulkan adapter probe attempts before the run fails")
    parser.add_argument("--base_port", type=int, default=None, help="Legacy per-worker port base")
    args = parser.parse_args(argv)
    if args.workers_per_gpu != 'auto':
        try:
            if int(args.workers_per_gpu) < 1:
                raise ValueError()
        except ValueError:
            parser.error('--workers-per-gpu must be auto or a positive integer')
    if args.shards_per_scene is not None and args.shards_per_scene < 1:
        parser.error("Worker and shard counts must be positive")
    if args.camera_export_workers != 'auto':
        try:
            if int(args.camera_export_workers) < 1:
                raise ValueError()
        except ValueError:
            parser.error('--camera-export-workers must be auto or a positive integer')
    if (args.worker_vram_mb < 1 or not 0 < args.vram_target <= 1 or not 0 < args.vram_budget_gb < float('inf')
            or (args.num_gpus is not None and args.num_gpus < 1)):
        parser.error('Invalid GPU allocation budget or GPU count')
    if args.task_limit is not None and args.task_limit < 1:
        parser.error("--task-limit must be positive")
    if args.timeout < 0 or args.seed < 0:
        parser.error("--timeout and --seed must be nonnegative")
    if args.max_retries < 0 or (args.max_failures is not None and args.max_failures < 1):
        parser.error("--max-retries and --max-failures must be nonnegative")
    if args.min_free_gb < 0:
        parser.error("--min-free-gb must be nonnegative")
    if args.probe_timeout <= 0 or args.probe_attempts < 1 or args.launch_stagger < 0:
        parser.error("Probe timeout/attempts and launch stagger must be positive")
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    config_path = resolve_path(args.config)
    config = json.loads(config_path.read_text(encoding="utf-8"))
    configure_runtime(config, argv, config_dir=config_path.parent)
    old_plan = None
    if args.resume:
        if not args.run_dir:
            parser.error('--resume requires --run-dir pointing to the existing plan')
        resume_dir = Path(args.run_dir).resolve()
        old_plan = json.loads((resume_dir / 'plan.json').read_text())
        supplied = {value.split('=')[0] for value in argv}
        # Restore recorded recipes; GPU/process counts are free to change.
        defaults = {'render_config': str(resume_dir / 'render.yaml'),
                    'trajectory_config': str(resume_dir / 'trajectory.yaml'),
                    'output_root': old_plan['output_root'],
                    'shards_per_scene': old_plan.get('shards_per_scene') or max(
                        int(j['id'].rsplit('_s', 1)[1]) + 1 for j in old_plan['jobs'])}
        worker_path = resume_dir / old_plan['jobs'][0]['id'] / 'worker.json'
        first_worker = json.loads(worker_path.read_text()) if worker_path.exists() else {}
        defaults['seed'] = old_plan.get('seed', first_worker.get('random_seed', 42))
        settings = first_worker.get('performance_settings', {})
        defaults['sync_profile'] = old_plan.get('sync_profile', next(
            (name for name, value in SYNC_PROFILES.items() if value == settings), 'legacy'))
        splits = (old_plan.get('dataset') or {}).get('split_counts', {})
        defaults['split'] = next(iter(splits)) if len(splits) == 1 else 'all'
        defaults['maps'] = sorted({j['map_package_path'] for j in old_plan['jobs']})
        defaults['targets'] = sorted({t for j in old_plan['jobs'] for t in j.get('target_names') or []}) or None
        for key, value in defaults.items():
            aliases = {'maps': {'-m'}, 'trajectory_config': set()}.get(key, set())
            if '--' + key.replace('_', '-') not in supplied and not aliases.intersection(supplied):
                setattr(args, key, value)
        if '--task-limit' not in supplied and old_plan.get('task_limit') is not None:
            args.task_limit = old_plan['task_limit']
    for key in ("ue_project", "ue_command", "python_script", "render_config"):
        config[key] = str(resolve_path(config[key], config_path.parent))
    project = resolve_path(config["ue_project"], config_path.parent)
    maps = get_maps(config, project, args.maps)
    trajectory_path = resolve_path(args.trajectory_config)
    trajectory = yaml.safe_load(trajectory_path.read_text(encoding="utf-8"))
    render = load_render_config(resolve_path(args.render_config or config["render_config"], config_path.parent))
    final_dataset = (project.parent / 'Config/Dataset/dataset_manifest.json').is_file()
    configured_output = Path(render['Output_Path']).expanduser()
    if not configured_output.is_absolute():
        configured_output = project.parent / configured_output
    if final_dataset:
        configured_output = configured_output / args.split
    output_root = Path(args.output_root or configured_output).expanduser().resolve()
    render["Output_Path"] = str(output_root)
    if args.dry_run and args.gpu_ids not in ("auto", "idle") and args.workers_per_gpu != 'auto':
        identities = [g.strip() for g in args.gpu_ids.split(",")]
        if not all(identities) or len(set(identities)) != len(identities):
            raise ValueError("GPU selection must be nonempty and contain no duplicates")
        if any(not g.isdigit() and not g.startswith("GPU-") for g in identities):
            raise ValueError("GPU selection must use physical NVIDIA indices or UUIDs")
        gpus = [{"index": int(g) if g.isdigit() else g, "uuid": g} for g in identities]
    else:
        gpus = choose_gpus(args.gpu_ids)
    if args.num_gpus:
        gpus = gpus[:args.num_gpus]
    counts = worker_allocation(gpus, args.workers_per_gpu, args.worker_vram_mb, args.vram_target,
                               args.vram_budget_gb * 1024)
    slot_gpus = [gpu for gpu, count in zip(gpus, counts) for _ in range(count)]
    slots = len(slot_gpus)
    shard_count = args.shards_per_scene or (3 if final_dataset else (slots * 2 if len(maps) < slots else 1))
    dataset_identity = None
    selected_rows = []
    if final_dataset:
        import dataset_plan
        manifest, dataset_identity = dataset_plan.installed_manifest(project)
        splits = ('train', 'test') if args.split == 'all' else (args.split,)
        if args.sequence_plan:
            plan_paths = [resolve_path(args.sequence_plan)]
        elif old_plan is not None:
            plan_paths = resume_sequence_plans(old_plan, project)
        else:
            plan_paths = []
        if not plan_paths:
            plan_paths = [project.parent / ('Config/Dataset/' + s + '_sequences.csv') for s in splits]
        jobs, summary, selected_rows = dataset_plan.jobs_from_csv(plan_paths, manifest, maps, args.targets,
            shard_count, args.task_limit, trajectory_tasks(trajectory))
        dataset_identity.update(summary)
    else:
        if args.sequence_plan:
            raise ValueError('A sequence plan requires the installed final dataset manifest')
        jobs = prepare_plan(config, trajectory, maps, shard_count, args.task_limit, args.targets)
    fingerprint_data = {"jobs": jobs, "render": render, "trajectory": trajectory,
                        "seed": args.seed, "sync_profile": args.sync_profile, "dataset": dataset_identity}
    fingerprint = hashlib.sha256(json.dumps(fingerprint_data, sort_keys=True).encode()).hexdigest()
    default_run = project.parent / "Saved/RenderRuns" / (time.strftime("%Y%m%d_%H%M%S") + "_" + uuid.uuid4().hex[:8])
    run_dir = Path(args.run_dir or default_run).resolve()
    plan = {"fingerprint": fingerprint, "run_dir": str(run_dir), "output_root": str(output_root),
            "gpus": gpus, "workers_per_gpu": counts[0] if len(set(counts)) == 1 else 'auto', "jobs": jobs,
            "gpu_worker_counts": counts, "total_workers": slots, "shards_per_scene": shard_count,
            "vram_budget_gib_per_gpu": args.vram_budget_gb, "worker_vram_mb": args.worker_vram_mb,
            "seed": args.seed, "sync_profile": args.sync_profile, "task_limit": args.task_limit,
            "camera_json": not args.skip_camera_json,
            "render_config": render, "trajectory_config": trajectory, "dataset": dataset_identity}
    if old_plan is not None:
        if not compatible_resume(old_plan, plan, args.seed, args.sync_profile):
            raise ValueError('Resume plan/configuration differs from the original image recipe')
        fingerprint = plan['fingerprint'] = old_plan['fingerprint']
        # Keep the exact legacy recipe behind its fingerprint. Deployment paths
        # may differ today; rewriting them would invalidate the next restart.
        for key in ('jobs', 'render_config', 'trajectory_config', 'dataset'):
            plan[key] = old_plan.get(key)
    camera_enabled = not args.skip_camera_json
    blender = None
    if camera_enabled:
        enabled_passes = {(p['pass_name'], p['ext']) for p in render['Render_Passes'] if p.get('enable')}
        if (render['Resolution'] != [512, 512] or trajectory['global']['num_frames'] != 25
                or render.get('Only_OCC') or render.get('Single_Frame_Only')
                or not {('rgb', 'png'), ('depth', 'exr')} <= enabled_passes
                or any(not j.get('target_names') for j in jobs)):
            raise ValueError('Camera JSON requires 25 views, native 512x512 RGB/depth, OCC+GT, and explicit targets; use --skip-camera-json for benchmarks')
        if not args.dry_run:
            from export_camera_json import blender_binary
            blender = blender_binary(args.blender)
    if args.dry_run:
        print(json.dumps(plan, indent=2))
        return 0
    if (run_dir / "plan.json").exists():
        old = json.loads((run_dir / "plan.json").read_text())
        if not args.resume or old["fingerprint"] != fingerprint:
            raise ValueError("Run directory already exists; use --resume with the same job/configuration")
    run_dir.mkdir(parents=True, exist_ok=True)
    output_root.mkdir(parents=True, exist_ok=True)
    resources.enter_context(exclusive_lock(output_root / '.render.lock'))
    resources.enter_context(exclusive_lock(run_dir / '.scheduler.lock'))
    dump_json(run_dir / "plan.json", plan)
    dump_json(run_dir / "launch.json", config)
    if selected_rows:
        dataset_plan.output_index(selected_rows, run_dir / 'sequence_index.csv')
    (run_dir / "trajectory.yaml").write_text(yaml.safe_dump(trajectory, sort_keys=False), encoding="utf-8")
    (run_dir / "render.yaml").write_text(yaml.safe_dump(render, sort_keys=False), encoding="utf-8")
    pending = queue.Queue()
    # Jobs whose images are complete and only lack camera JSON. They need no
    # GPU, so dedicated CPU threads drain them in parallel with rendering.
    camera_pending = queue.Queue()
    results = []
    resumed = []
    def resumable(job):
        """Classify a job as ('done', marker), ('camera', None) or ('render', None)."""
        if not args.resume:
            return 'render', None
        marker = run_dir / job["id"] / "done.json"
        valid_frames = True
        if final_dataset:
            try:
                valid_frames = not remaining_render_job(output_root, job, render)['trajectory_tasks']
            except (OSError, ValueError):
                valid_frames = False
        if not valid_frames:
            return 'render', None
        valid_camera = not camera_enabled or all(camera_pair_valid(c) for c in camera_jobs(output_root, job))
        if not valid_camera:
            return ('camera', None) if final_dataset else ('render', None)
        if not marker.is_file():
            return 'render', None
        try:
            old = json.loads(marker.read_text())
            if not isinstance(old, dict):
                old = {}
        except (OSError, ValueError):
            old = {}
        if old.get("fingerprint") == fingerprint and output_inventory(output_root, job) == old.get("output"):
            return 'done', old
        return 'render', None

    # Validation is per-job file-system latency (about 5s per job on CPFS);
    # done serially it kept every GPU idle for minutes at each restart.
    with concurrent.futures.ThreadPoolExecutor(max_workers=16) as validators:
        for job, (state, old) in zip(jobs, validators.map(resumable, jobs)):
            if state == 'render':
                pending.put(job)
            elif state == 'camera':
                camera_pending.put(job)
            else:
                logging.info("RESUME_SKIP %s", job["id"])
                results.append(old)
                resumed.append(job['id'])
    dump_json(run_dir / 'progress.json', {'completed': len(resumed), 'planned': len(jobs),
                                         'resumed': len(resumed), 'failed': 0, 'results': results})
    camera_workers = (max(1, (os.cpu_count() or 2) // BLENDER_THREADS)
                      if args.camera_export_workers == 'auto' else int(args.camera_export_workers))
    logging.info('SCHEDULE GPUs=%s workers=%s resumed=%s pending=%s camera_only=%s camera_json=%s camera_workers=%s',
                 [g['index'] for g in gpus], counts, len(resumed), pending.qsize(), camera_pending.qsize(),
                 camera_enabled, camera_workers if camera_enabled else 0)
    camera_slots = threading.Semaphore(camera_workers)
    lock = threading.Lock()
    allocated_ports = set()
    failed = threading.Event()
    cancelled = threading.Event()
    stop_requested = threading.Event()
    stop_signal = None
    active = {}
    begin = time.monotonic()
    run_token = "r_" + hashlib.sha256(str(run_dir).encode()).hexdigest()[:16]

    def cancel_active_workers():
        with lock:
            if cancelled.is_set():
                return
            cancelled.set()
            processes = list(active.values())
        for process, kind in processes:
            try:
                if kind == 'camera':
                    os.killpg(process.pid, signal.SIGTERM)
                else:
                    process.send_signal(signal.SIGINT)
            except ProcessLookupError:
                pass

    # One Vulkan probe per run, shared by every worker. A launch storm of
    # per-worker probes can exceed the probe timeout and used to abort the
    # entire dataset; camera-only jobs never need this at all.
    device_map_lock = threading.Lock()
    device_map = {"ready": threading.Event(), "value": None, "error": None}
    device_map_path = run_dir / "device_bindings.json"

    def ensure_device_map():
        """Probe every scheduled GPU once; return the manifest or None.

        A probe failure must not abort the dataset: workers still fall back to
        a local probe (with retries) and only the affected job pays for it.
        """
        with device_map_lock:
            if device_map["ready"].is_set():
                return device_map["value"]
            try:
                from gpu_devices import build_device_map
                manifest = build_device_map(gpus, timeout=args.probe_timeout,
                                            attempts=args.probe_attempts)
                # Always rewrite: a restarted or resumed scheduler must not
                # inherit adapter indices from an earlier node or driver.
                dump_json(device_map_path, manifest)
                logging.info("DEVICE_MAP %s", json.dumps({"host": manifest["host"],
                             "gpus": manifest["gpus"], "timings": manifest["probe"]["timings"]},
                             sort_keys=True))
                device_map["value"] = manifest
            except Exception as error:
                logging.exception("DEVICE_MAP_FAILED %s; workers will probe individually",
                                  error.__class__.__name__)
            finally:
                device_map["ready"].set()
            return device_map["value"]

    attempts = {}
    permanent_failures = []
    failure_budget = (args.max_failures if args.max_failures is not None
                      else max(3, -(-len(jobs) // 100)))

    def fail_run():
        failed.set()
        if not args.keep_going:
            cancel_active_workers()

    def retry(job, attempt, reason, target=None):
        """Requeue a failed job until its attempt budget is exhausted.

        Exhausting one job's retries is not a run-level event: it is counted
        against the failure budget, and only crossing that budget stops the
        run. Stopping on the first exhaustion would make ``--max-failures``
        meaningless and turn one bad map into a cancelled dataset.
        """
        if attempt + 1 > args.max_retries:
            permanent_failures.append(job["id"])
            logging.error("RETRY_EXHAUSTED %s attempts=%s reason=%s", job["id"], attempt + 1, reason)
            if len(permanent_failures) >= failure_budget:
                logging.error("FAILURE_BUDGET %s jobs failed; stopping the run",
                              len(permanent_failures))
                fail_run()
            return False
        logging.warning("RETRY %s attempt=%s/%s reason=%s", job["id"], attempt + 1,
                        args.max_retries + 1, reason)
        (pending if target is None else target).put(job)
        return True

    launch_lock = threading.Lock()
    next_launch = [time.monotonic()]

    def pace_launch():
        """Space consecutive UE launches by ``--launch-stagger``.

        Pacing happens at launch time, after the shared device probe: a slow
        or failed probe must not release every waiting worker at once.
        """
        with launch_lock:
            now = time.monotonic()
            at = max(now, next_launch[0])
            next_launch[0] = at + args.launch_stagger
        # Wake on cancellation too: a failed run must not keep queued
        # launchers sleeping through the scheduler's stop deadline.
        while time.monotonic() < at and not scheduling_stopped():
            stop_requested.wait(min(0.2, at - time.monotonic()))

    def scheduling_stopped():
        return (stop_requested.is_set() or cancelled.is_set()
                or (failed.is_set() and not args.keep_going))

    disk_low = threading.Event()

    def enough_disk():
        """Drain instead of failing mid-job when run logs or outputs run out of space.

        A full filesystem fails marker/progress writes and cancels every
        active render; stopping new launches lets running workers finish.
        """
        for path in (run_dir, output_root):
            try:
                free = shutil.disk_usage(path).free
            except OSError as error:
                logging.warning("DISK_CHECK_FAILED %s: %s", path, error)
                continue
            if free < args.min_free_gb * 1024 ** 3:
                if not disk_low.is_set():
                    disk_low.set()
                    logging.error("DISK_LOW %s has %.1f GiB free (< %s GiB); no new jobs will start",
                                  path, free / 1024 ** 3, args.min_free_gb)
                return False
        return True

    retired_slots = []
    rendered_tasks = {}

    def worker(slot, gpu):
        """Render on ``gpu``; with ``gpu=None`` only export camera JSON for finished renders."""
        source = pending if gpu is not None else camera_pending
        gpu_label = gpu["index"] if gpu is not None else 'cpu'
        while not scheduling_stopped():
            if disk_low.is_set() or not enough_disk():
                return
            try:
                job = source.get_nowait()
            except queue.Empty:
                return
            attempt = attempts.get(job["id"], 0)
            job_dir = run_dir / job["id"]
            proc = None
            proc_kind = 'render'
            requeued = False
            retire_slot = False
            launched_tasks = 0
            reason = None
            start = time.monotonic()
            try:
                job_dir.mkdir(exist_ok=True)
                # A retry only re-renders sequences the failed attempt left incomplete.
                render_job = (remaining_render_job(output_root, job, render)
                              if (args.resume or attempt or gpu is None) and final_dataset else job)
                if gpu is None and render_job['trajectory_tasks']:
                    # Images went missing after classification; a GPU slot owns it now.
                    logging.warning("CAMERA_ONLY_NEEDS_RENDER %s; moved to the render queue", job["id"])
                    pending.put(job)
                    continue
                if render_job['trajectory_tasks'] and render_job is not job:
                    # Incomplete sequences are not inspected or repaired:
                    # delete their images, pose JSON and FBX, then render anew.
                    for sequence in camera_jobs(output_root, render_job):
                        for directory in (sequence['GT_dir'], sequence['occ_dir']):
                            shutil.rmtree(directory, ignore_errors=True)
                        Path(sequence['fbx_path']).unlink(missing_ok=True)
                (job_dir / 'done.json').unlink(missing_ok=True)
                options = {"map_package_path": job["map_package_path"],
                       "trajectory_tasks": render_job["trajectory_tasks"],
                       "trajectory_config_path": str(run_dir / "trajectory.yaml"),
                       "sequence_root": "/Game/Sequences/_workers/{}/{}".format(run_token, job["id"]),
                       "random_seed": args.seed,
                       "telemetry_path": str(job_dir / "steps.jsonl"),
                       "export_output_path": str(output_root / "Exported_FBX"),
                       "performance_settings": SYNC_PROFILES[args.sync_profile]}
                if job.get('target_names'):
                    options['target_names'] = job['target_names']
                if 'occlusion_actor_names' in job:
                    options['occlusion_actor_names'] = job['occlusion_actor_names']
                dump_json(job_dir / "worker.json", options)
                returncode = 0
                teardown_incomplete = False
                logging.info("START %s gpu=%s slot=%s tasks=%s reused_tasks=%s attempt=%s",
                             job["id"], gpu_label, slot, len(render_job['trajectory_tasks']),
                             len(job['trajectory_tasks']) - len(render_job['trajectory_tasks']), attempt + 1)
                if render_job['trajectory_tasks']:
                    port = args.base_port + slot if args.base_port is not None else allocate_port(allocated_ports, lock)
                    command = [sys.executable, str(PLUGIN_ROOT / "misc/run_cmd_async.py"),
                           "--config_file", str(run_dir / "launch.json"), "--gpu_id", str(gpu["index"]),
                           "--gpu-uuid", str(gpu["uuid"]),
                           "--port", str(port), "--ue-map", job["map_package_path"],
                           "--render-config", str(run_dir / "render.yaml"),
                           "--worker-config", str(job_dir / "worker.json"), "--headless",
                           "--log-file", str(job_dir / "ue.log"), "--timeout", str(args.timeout),
                           "--probe-timeout", str(args.probe_timeout),
                           "--probe-attempts", str(args.probe_attempts),
                           "--probe-lock", str(run_dir / "vulkan_probe.lock")]
                    if ensure_device_map() is not None:
                        command += ["--device-map", str(device_map_path)]
                    pace_launch()
                    with (job_dir / "launcher.log").open("ab") as log:
                        if stop_requested.is_set() or cancelled.is_set():
                            raise InterruptedError("Scheduler stop requested before worker launch")
                        proc = subprocess.Popen(command, cwd=str(PLUGIN_ROOT), stdout=log, stderr=log)
                        launched_tasks = len(render_job['trajectory_tasks'])
                        with lock:
                            active[slot] = (proc, 'render')
                            if cancelled.is_set():
                                try:
                                    proc.send_signal(signal.SIGINT)
                                except ProcessLookupError:
                                    pass
                        returncode = proc.wait()
                        with lock:
                            active.pop(slot, None)
                        if returncode == EXIT_WORKER_UNREAPED:
                            # The killed UE still holds GPU memory and driver
                            # state; launching another one here would stack on it.
                            retire_slot = True
                            with lock:
                                retired_slots.append({"slot": slot, "gpu": gpu_label, "job": job["id"]})
                            logging.error("SLOT_RETIRED slot=%s gpu=%s job=%s: UE not reaped after SIGKILL",
                                          slot, gpu_label, job["id"])
                        if returncode in SUPERVISOR_STATUSES and final_dataset:
                            # The rc=0 checks below validate the outputs; a
                            # retry would find nothing left to render. An
                            # incomplete job fails them and is retried.
                            logging.warning("RENDER_TEARDOWN_INCOMPLETE %s rc=%s: validating outputs",
                                            job["id"], returncode)
                            teardown_incomplete = True
                            returncode = 0
                        if returncode:
                            reason = "render rc={}".format(returncode)
                if returncode == 0 and final_dataset:
                    dataset_plan.check_output(output_root, job, render)
                    if remaining_render_job(output_root, job, render)['trajectory_tasks']:
                        raise ValueError('Worker did not produce all planned FBX files')
                if returncode == 0:
                    dump_json(job_dir / 'render_done.json', {'fingerprint': fingerprint, 'id': job['id']})
                if returncode == 0 and camera_enabled:
                    export_jobs = [c for c in camera_jobs(output_root, job) if not camera_pair_valid(c)]
                    if export_jobs:
                        dump_json(job_dir / 'camera_jobs.json', export_jobs)
                        export_command = [sys.executable, str(SCRIPT_DIR / 'export_camera_json.py'),
                            '--output-root', str(output_root), '--jobs-file', str(job_dir / 'camera_jobs.json'),
                            '--report', str(job_dir / 'camera_export_report.json'), '--blender', blender,
                            '--camera-name', config.get('camera_name', 'CineCameraActor1'), '--overwrite']
                        while not camera_slots.acquire(timeout=0.2):
                            if stop_requested.is_set() or cancelled.is_set():
                                raise InterruptedError('Scheduler stopped before camera export')
                        try:
                            if stop_requested.is_set() or cancelled.is_set():
                                raise InterruptedError('Scheduler stopped before camera export')
                            logging.info('CAMERA_EXPORT %s sequences=%s', job['id'], len(export_jobs))
                            proc_kind = 'camera'
                            with (job_dir / 'camera_export.log').open('ab') as log:
                                proc = subprocess.Popen(export_command, stdout=log, stderr=log, start_new_session=True)
                                with lock:
                                    active[slot] = (proc, 'camera')
                                    if cancelled.is_set():
                                        try:
                                            os.killpg(proc.pid, signal.SIGTERM)
                                        except ProcessLookupError:
                                            pass
                                returncode = proc.wait()
                                with lock:
                                    active.pop(slot, None)
                            if returncode:
                                raise RuntimeError('Camera JSON export failed (rc={}): {}'.format(
                                    returncode, job_dir / 'camera_export.log'))
                        finally:
                            camera_slots.release()
                    if not all(camera_pair_valid(c) for c in camera_jobs(output_root, job)):
                        raise ValueError('Camera JSON validation failed after export')
                if stop_requested.is_set() or cancelled.is_set():
                    if returncode == 0:
                        raise InterruptedError("Scheduler stop requested before worker launch")
                inventory = output_inventory(output_root, job)
                result = {"id": job["id"], "gpu": gpu_label, "returncode": returncode,
                      "wall_seconds": round(time.monotonic() - start, 3), "output": inventory,
                      "fingerprint": fingerprint, 'camera_json': camera_enabled}
                if teardown_incomplete:
                    result['teardown_incomplete'] = True
                if returncode == 0 and not inventory["image_files"]:
                    result["returncode"] = returncode = 1
                    result["error"] = reason = "Worker returned success without images"
            except Exception as error:
                logging.exception("Worker task failed: %s", job["id"])
                result = {"id": job["id"], "gpu": gpu_label, "returncode": 1,
                          "wall_seconds": round(time.monotonic() - start, 3), "error": str(error),
                          "output": {"image_files": 0, "bytes": 0, "frame_views": 0, "directories": []}}
                returncode = 1
                reason = "{}: {}".format(error.__class__.__name__, error)
            finally:
                with lock:
                    active.pop(slot, None)
                if proc is not None and proc.poll() is None:
                    if proc_kind == 'camera':
                        os.killpg(proc.pid, signal.SIGTERM)
                    elif not cancelled.is_set():
                        proc.send_signal(signal.SIGINT)
                    try:
                        # Let the supervisor's bounded owned cleanup finish.
                        proc.wait(timeout=WORKER_STOP_SECONDS)
                    except subprocess.TimeoutExpired:
                        if proc_kind == 'camera':
                            os.killpg(proc.pid, signal.SIGKILL)
                        else:
                            proc.kill()
                        proc.wait()
                source.task_done()
            # Throughput counts every attempt's newly valid sequences: a retry
            # that only backfills camera JSON must not erase earlier renders.
            if launched_tasks:
                completed_tasks = launched_tasks if returncode == 0 else 0
                if returncode and final_dataset:
                    try:
                        completed_tasks = launched_tasks - len(
                            remaining_render_job(output_root, job, render)['trajectory_tasks'])
                    except Exception:
                        logging.exception("Could not count completed sequences: %s", job["id"])
                with lock:
                    rendered_tasks[job["id"]] = rendered_tasks.get(job["id"], 0) + max(0, completed_tasks)
            result['rendered_tasks'] = rendered_tasks.get(job["id"], 0)
            result['rendered_image_files'] = (result['output']['image_files'] * result['rendered_tasks']
                                              // len(job['trajectory_tasks']))
            # A failure during a scheduler stop is a cancellation, not evidence
            # against the job: record it, but never requeue or count it.
            stopping = stop_requested.is_set() or cancelled.is_set()
            if returncode and not stopping:
                attempts[job["id"]] = attempt + 1
                result["retry_attempt"] = attempt + 1
                requeued = result["requeued"] = retry(
                    job, attempt, reason, camera_pending if gpu is None else None)
            if requeued:
                # Attempt evidence only: the job is already back in the queue,
                # so the run's result list waits for its final attempt.
                try:
                    dump_json(job_dir / "failed.json", result)
                except OSError:
                    fail_run()
                    logging.exception("Could not write worker marker")
                logging.info("END %s gpu=%s rc=%s %.1fs images=%s retry=%s", job["id"], gpu_label,
                             returncode, result["wall_seconds"], result["output"]["image_files"], attempt + 1)
                if retire_slot:
                    return
                continue
            try:
                dump_json(job_dir / ("done.json" if returncode == 0 else "failed.json"), result)
            except OSError:
                fail_run()
                logging.exception("Could not write worker marker")
            progress_failed = False
            with lock:
                results.append(result)
                try:
                    dump_json(run_dir / "progress.json", {
                        "completed": sum(r['returncode'] == 0 for r in results), "planned": len(jobs),
                        "resumed": len(resumed), "failed": sum(r['returncode'] != 0 for r in results),
                        "attempted_this_run": len(results) - len(resumed), "results": results})
                except OSError:
                    progress_failed = True
                    logging.exception("Could not write progress")
            if progress_failed:
                fail_run()
            logging.info("END %s gpu=%s rc=%s %.1fs images=%s", job["id"], gpu_label, returncode, result["wall_seconds"], result["output"]["image_files"])
            if retire_slot:
                return

    def request_stop(signum, _frame):
        nonlocal stop_signal
        # Do not raise through Thread.join(), acquire scheduler locks, or
        # signal children here. Main-loop cleanup handles those operations.
        if stop_signal is None:
            stop_signal = signum
        stop_requested.set()

    previous_handlers = {sig: signal.getsignal(sig)
                         for sig in (signal.SIGINT, signal.SIGTERM)}
    for sig in previous_handlers:
        signal.signal(sig, request_stop)
    threads = []
    stop_deadline = None
    cleanup_timed_out = False
    try:
        for slot in range(slots):
            if stop_requested.is_set():
                break
            thread = threading.Thread(target=worker, args=(slot, slot_gpus[slot]), daemon=True)
            thread.start()
            threads.append(thread)
        # Camera-only backfill is CPU work: it scales with cores, not GPU slots.
        for index in range(min(camera_workers, camera_pending.qsize()) if camera_enabled else 0):
            if stop_requested.is_set():
                break
            thread = threading.Thread(target=worker, args=(slots + index, None), daemon=True)
            thread.start()
            threads.append(thread)
        while any(t.is_alive() for t in threads):
            if stop_requested.is_set() and stop_deadline is None:
                logging.warning("SCHEDULER_STOP_REQUEST signal=%s", signal.Signals(stop_signal).name)
                failed.set()
                cancel_active_workers()
                stop_deadline = time.monotonic() + WORKER_STOP_SECONDS
            if stop_deadline is not None and time.monotonic() >= stop_deadline:
                cleanup_timed_out = True
                logging.error("Worker cleanup exceeded the %s-second stop deadline", WORKER_STOP_SECONDS)
                break
            for thread in threads:
                thread.join(timeout=0.2)
        wall = time.monotonic() - begin
        with lock:
            final_results = list(results)
        failed_jobs = [r["id"] for r in final_results if r["returncode"] != 0]
        # Jobs still queued when every worker exited, e.g. a retry requeued by
        # the last live slot just before that slot was retired.
        unscheduled = []
        for leftover in (pending, camera_pending):
            while True:
                try:
                    unscheduled.append(leftover.get_nowait()["id"])
                except queue.Empty:
                    break
        if unscheduled and not stop_requested.is_set():
            logging.error("UNSCHEDULED %s jobs left in the queue: %s", len(unscheduled), unscheduled)
        # ``failed`` means the run was aborted (budget or a fatal scheduler
        # error). A job that exhausted its own retries without tripping the
        # budget does not abort the dataset, but it does leave it incomplete,
        # so the exit status must still report failure.
        result = {"success": (not failed.is_set() and not stop_requested.is_set()
                              and not unscheduled and not failed_jobs),
                  "wall_seconds": round(wall, 3),
                  "attempted_this_run": len(final_results) - len(resumed),
                  "completed_this_run": sum(r['returncode'] == 0 for r in final_results[len(resumed):]),
                  "resumed": len(resumed), 'completed': sum(r['returncode'] == 0 for r in final_results),
                  "planned": len(jobs), "results": final_results,
                  "failed_jobs": failed_jobs, "failed_count": len(failed_jobs),
                  "unscheduled_jobs": unscheduled, "retired_slots": list(retired_slots),
                  "disk_low": disk_low.is_set(),
                  "image_files": sum(r["output"]["image_files"] for r in final_results),
                  "stop_signal": signal.Signals(stop_signal).name if stop_signal is not None else None,
                  "cleanup_timed_out": cleanup_timed_out}
        current_images = sum(r.get('rendered_image_files', 0) for r in final_results[len(resumed):])
        result["image_files_per_second"] = current_images / wall if wall else 0
        dump_json(run_dir / "result.json", result)
        print(json.dumps(result, indent=2))
        if stop_signal is not None:
            return 128 + stop_signal
        return 0 if result["success"] else 1
    finally:
        for sig, handler in previous_handlers.items():
            signal.signal(sig, handler)


def main(argv=None):
    with ExitStack() as resources:
        return _main(argv, resources)


if __name__ == "__main__":
    raise SystemExit(main())
