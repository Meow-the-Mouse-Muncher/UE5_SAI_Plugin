"""Dynamically schedule disjoint scene/trajectory shards on selected physical GPUs."""
import argparse
import hashlib
import json
import logging
import os
import queue
import signal
import socket
import subprocess
import sys
import threading
import time
import uuid
from pathlib import Path

import yaml

from pipeline_config import (PLUGIN_ROOT, dump_json, load_render_config, partition_tasks,
                             resolve_map, resolve_path, trajectory_tasks)


SYNC_PROFILES = {
    "legacy": {},
    "balanced": {"queue_wait_cycles": 1, "queue_wait_seconds": 0.25,
                 "step_wait_seconds": 0.1, "step_collect_garbage": False,
                 "map_wait_cycles": 2, "map_wait_seconds": 0.5,
                 "exit_wait_cycles": 1, "exit_wait_seconds": 0.5},
}


def choose_gpus(requested):
    from gpu_devices import find_gpu, gpu_inventory
    inventory = gpu_inventory()
    if requested == "auto":
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


def allocate_port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def output_inventory(root, job):
    """Account only for this task's disjoint canonical output prefixes."""
    root = Path(root)
    total = size = frames = 0
    prefixes = []
    map_name = job["map_package_path"].rsplit("/", 1)[-1]
    for kind, height, angle in job["trajectory_tasks"]:
        folder = root / kind
        for directory in folder.glob(map_name + "_Target_*_height_{:03d}_ang_{:03d}_*".format(int(height / 100), int(angle))):
            if job.get("target_names") and not any((map_name + "_" + target + "_") in directory.name for target in job["target_names"]):
                continue
            stems = set()
            for path in directory.rglob("*"):
                if path.is_file() and path.suffix.lower() in (".png", ".exr", ".jpg", ".jpeg"):
                    total += 1
                    size += path.stat().st_size
                    stems.add(path.stem)
            frames += len(stems)
            prefixes.append(directory.name)
    return {"image_files": total, "bytes": size, "frame_views": frames, "directories": sorted(prefixes)}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", "-c", default=str(PLUGIN_ROOT / "misc/user.json"))
    parser.add_argument("--gpu-ids", "--gpu_ids", default="auto")
    parser.add_argument("--workers-per-gpu", "--processes_per_gpu", type=int, default=1)
    parser.add_argument("--num_gpus", "--num-gpus", type=int, default=None, help="Legacy limit; --gpu-ids auto still excludes busy cards")
    parser.add_argument("--maps", "-m", nargs="+")
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
    parser.add_argument("--keep-going", action="store_true")
    parser.add_argument("--base_port", type=int, default=None, help="Legacy per-worker port base")
    args = parser.parse_args(argv)
    if args.workers_per_gpu < 1 or (args.shards_per_scene is not None and args.shards_per_scene < 1):
        parser.error("Worker and shard counts must be positive")
    if args.task_limit is not None and args.task_limit < 1:
        parser.error("--task-limit must be positive")
    if args.timeout < 0 or args.seed < 0:
        parser.error("--timeout and --seed must be nonnegative")
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    config_path = resolve_path(args.config)
    config = json.loads(config_path.read_text(encoding="utf-8"))
    for key in ("ue_project", "ue_command", "python_script", "render_config"):
        config[key] = str(resolve_path(config[key], config_path.parent))
    project = resolve_path(config["ue_project"], config_path.parent)
    maps = get_maps(config, project, args.maps)
    trajectory_path = resolve_path(args.trajectory_config)
    trajectory = yaml.safe_load(trajectory_path.read_text(encoding="utf-8"))
    render = load_render_config(resolve_path(args.render_config or config["render_config"], config_path.parent))
    output_root = Path(args.output_root or render["Output_Path"]).expanduser().resolve()
    render["Output_Path"] = str(output_root)
    if args.dry_run and args.gpu_ids != "auto":
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
    slots = len(gpus) * args.workers_per_gpu
    shard_count = args.shards_per_scene or (slots * 2 if len(maps) < slots else 1)
    jobs = prepare_plan(config, trajectory, maps, shard_count, args.task_limit, args.targets)
    fingerprint_data = {"jobs": jobs, "render": render, "trajectory": trajectory,
                        "seed": args.seed, "sync_profile": args.sync_profile}
    fingerprint = hashlib.sha256(json.dumps(fingerprint_data, sort_keys=True).encode()).hexdigest()
    default_run = project.parent / "Saved/RenderRuns" / (time.strftime("%Y%m%d_%H%M%S") + "_" + uuid.uuid4().hex[:8])
    run_dir = Path(args.run_dir or default_run).resolve()
    plan = {"fingerprint": fingerprint, "run_dir": str(run_dir), "output_root": str(output_root),
            "gpus": gpus, "workers_per_gpu": args.workers_per_gpu, "jobs": jobs,
            "render_config": render, "trajectory_config": trajectory}
    if args.dry_run:
        print(json.dumps(plan, indent=2))
        return 0
    if (run_dir / "plan.json").exists():
        old = json.loads((run_dir / "plan.json").read_text())
        if not args.resume or old["fingerprint"] != fingerprint:
            raise ValueError("Run directory already exists; use --resume with the same job/configuration")
    run_dir.mkdir(parents=True, exist_ok=True)
    output_root.mkdir(parents=True, exist_ok=True)
    dump_json(run_dir / "plan.json", plan)
    dump_json(run_dir / "launch.json", config)
    (run_dir / "trajectory.yaml").write_text(yaml.safe_dump(trajectory, sort_keys=False), encoding="utf-8")
    (run_dir / "render.yaml").write_text(yaml.safe_dump(render, sort_keys=False), encoding="utf-8")
    pending = queue.Queue()
    for job in jobs:
        marker = run_dir / job["id"] / "done.json"
        if args.resume and marker.is_file():
            old = json.loads(marker.read_text())
            current = output_inventory(output_root, job)
            if old.get("fingerprint") == fingerprint and current == old.get("output"):
                logging.info("RESUME_SKIP %s", job["id"])
                continue
        pending.put(job)
    results = []
    lock = threading.Lock()
    failed = threading.Event()
    cancelled = threading.Event()
    active = {}
    begin = time.monotonic()
    run_token = "r_" + hashlib.sha256(str(run_dir).encode()).hexdigest()[:16]

    def worker(slot, gpu):
        while not (failed.is_set() and not args.keep_going):
            try:
                job = pending.get_nowait()
            except queue.Empty:
                return
            job_dir = run_dir / job["id"]
            proc = None
            start = time.monotonic()
            try:
                job_dir.mkdir(exist_ok=True)
                options = {"map_package_path": job["map_package_path"],
                       "trajectory_tasks": job["trajectory_tasks"],
                       "trajectory_config_path": str(run_dir / "trajectory.yaml"),
                       "sequence_root": "/Game/Sequences/_workers/{}/{}".format(run_token, job["id"]),
                       "random_seed": args.seed,
                       "telemetry_path": str(job_dir / "steps.jsonl"),
                       "export_output_path": str(output_root / "Exported_FBX"),
                       "performance_settings": SYNC_PROFILES[args.sync_profile]}
                if args.targets:
                    options["target_names"] = args.targets
                dump_json(job_dir / "worker.json", options)
                port = args.base_port + slot if args.base_port is not None else allocate_port()
                command = [sys.executable, str(PLUGIN_ROOT / "misc/run_cmd_async.py"),
                       "--config_file", str(run_dir / "launch.json"), "--gpu_id", str(gpu["index"]),
                       "--port", str(port), "--ue-map", job["map_package_path"],
                       "--render-config", str(run_dir / "render.yaml"),
                       "--worker-config", str(job_dir / "worker.json"), "--headless",
                       "--log-file", str(job_dir / "ue.log"), "--timeout", str(args.timeout)]
                logging.info("START %s gpu=%s slot=%s tasks=%s", job["id"], gpu["index"], slot, len(job["trajectory_tasks"]))
                with (job_dir / "launcher.log").open("wb") as log:
                    proc = subprocess.Popen(command, cwd=str(PLUGIN_ROOT), stdout=log, stderr=log)
                    with lock:
                        active[slot] = proc
                        if cancelled.is_set():
                            try:
                                proc.send_signal(signal.SIGINT)
                            except ProcessLookupError:
                                pass
                    returncode = proc.wait()
                inventory = output_inventory(output_root, job)
                result = {"id": job["id"], "gpu": gpu["index"], "returncode": returncode,
                      "wall_seconds": round(time.monotonic() - start, 3), "output": inventory,
                      "fingerprint": fingerprint}
                if returncode == 0 and not inventory["image_files"]:
                    result["returncode"] = returncode = 1
                    result["error"] = "Worker returned success without images"
            except Exception as error:
                failed.set()
                logging.exception("Worker task failed: %s", job["id"])
                result = {"id": job["id"], "gpu": gpu["index"], "returncode": 1,
                          "wall_seconds": round(time.monotonic() - start, 3), "error": str(error),
                          "output": {"image_files": 0, "bytes": 0, "frame_views": 0, "directories": []}}
                returncode = 1
            finally:
                with lock:
                    active.pop(slot, None)
                if proc is not None and proc.poll() is None:
                    proc.send_signal(signal.SIGINT)
                    try:
                        proc.wait(timeout=20)
                    except subprocess.TimeoutExpired:
                        proc.kill()
                        proc.wait()
                pending.task_done()
            if returncode:
                failed.set()
            try:
                dump_json(job_dir / ("done.json" if returncode == 0 else "failed.json"), result)
            except OSError:
                failed.set()
                logging.exception("Could not write worker marker")
            with lock:
                results.append(result)
                try:
                    dump_json(run_dir / "progress.json", {"completed": len(results), "planned": len(jobs), "results": results})
                except OSError:
                    failed.set()
                    logging.exception("Could not write progress")
            logging.info("END %s gpu=%s rc=%s %.1fs images=%s", job["id"], gpu["index"], returncode, result["wall_seconds"], result["output"]["image_files"])

    threads = []
    try:
        for slot in range(slots):
            thread = threading.Thread(target=worker, args=(slot, gpus[slot // args.workers_per_gpu]), daemon=True)
            thread.start()
            threads.append(thread)
        while any(t.is_alive() for t in threads):
            for thread in threads:
                thread.join(timeout=0.2)
    except KeyboardInterrupt:
        cancelled.set()
        failed.set()
        with lock:
            for proc in active.values():
                try:
                    proc.send_signal(signal.SIGINT)
                except ProcessLookupError:
                    pass
        for thread in threads:
            thread.join(timeout=20)
        raise
    wall = time.monotonic() - begin
    result = {"success": not failed.is_set() and pending.empty(), "wall_seconds": round(wall, 3),
              "completed_this_run": len(results), "planned": len(jobs), "results": results,
              "image_files": sum(r["output"]["image_files"] for r in results)}
    result["image_files_per_second"] = result["image_files"] / wall if wall else 0
    dump_json(run_dir / "result.json", result)
    print(json.dumps(result, indent=2))
    return 0 if result["success"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
