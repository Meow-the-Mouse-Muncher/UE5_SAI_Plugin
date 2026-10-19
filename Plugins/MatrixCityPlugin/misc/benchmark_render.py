"""Run a renderer with GPU, output-throughput and process-placement telemetry.

Example:
  python misc/benchmark_render.py --gpu-id 1 --output-dir /tmp/render-output \
      --result-dir /tmp/render-metrics --label baseline --timeout 1800 -- \
      python misc/run_cmd_async.py --gpu_id 1 --config_file /tmp/benchmark.json

The target GPU must be idle at launch. Existing jobs are never stopped. Only
process groups created by this benchmark and its verified descendants are
terminated on timeout, interruption or incorrect GPU placement.
"""
import argparse
import csv
import datetime
import json
import os
from pathlib import Path
import signal
import statistics
import subprocess
import sys
import time
import xml.etree.ElementTree as ET

from gpu_devices import find_gpu, gpu_inventory


def output_snapshot(root):
    """Image files only; stat fingerprints distinguish new/overwritten output."""
    root = Path(root)
    result = {}
    if not root.exists():
        return result
    for folder, _, filenames in os.walk(root, followlinks=False):
        for name in filenames:
            extension = Path(name).suffix.lower()
            if extension not in (".png", ".exr"):
                continue
            path = Path(folder) / name
            try:
                stat = path.stat()
            except FileNotFoundError:
                continue
            relative = str(path.relative_to(root))
            result[relative] = (stat.st_size, stat.st_mtime_ns)
    return result


def output_totals(before, current):
    totals = {"rgb_png": 0, "depth_exr": 0, "other_exr": 0,
              "image_files": 0, "image_bytes": 0}
    for relative, fingerprint in current.items():
        if before.get(relative) == fingerprint:
            continue
        extension = Path(relative).suffix.lower()
        parts = [part.lower() for part in Path(relative).parts]
        if extension == ".png":
            totals["rgb_png"] += 1
        elif any("depth" in part for part in parts):
            totals["depth_exr"] += 1
        else:
            totals["other_exr"] += 1
        totals["image_files"] += 1
        totals["image_bytes"] += fingerprint[0]
    return totals


def gpu_sample(gpu_uuid):
    fields = ("utilization.gpu", "utilization.memory", "memory.used", "power.draw",
              "clocks.sm", "temperature.gpu")
    result = subprocess.run(
        ["nvidia-smi", "-i", gpu_uuid, "--query-gpu=" + ",".join(fields),
         "--format=csv,noheader,nounits"],
        check=True, capture_output=True, text=True, timeout=10)
    row = next(csv.reader(result.stdout.splitlines()))
    names = ("gpu_util_percent", "memory_util_percent", "gpu_memory_mb", "power_w",
             "sm_clock_mhz", "temperature_c")
    sample = {}
    for name, value in zip(names, row):
        try:
            sample[name] = float(value.strip())
        except ValueError:
            sample[name] = None
    return sample


def _process_table():
    """Read PID ancestry and start times, without reading process arguments."""
    table = {}
    if not Path("/proc").is_dir():
        return table
    for folder in Path("/proc").iterdir():
        if not folder.name.isdigit():
            continue
        try:
            text = (folder / "stat").read_text()
            fields = text[text.rindex(")") + 2:].split()
            table[int(folder.name)] = {"ppid": int(fields[1]),
                                       "pgrp": int(fields[2]),
                                       "start": int(fields[19])}
        except (OSError, ValueError, IndexError):
            continue
    return table


def _update_owned(root_pid, known):
    table = _process_table()
    owned = {pid for pid, start in known.items()
             if pid in table and table[pid]["start"] == start}
    if (root_pid in table
            and (root_pid not in known or table[root_pid]["start"] == known[root_pid])):
        owned.add(root_pid)
    while True:
        additions = {pid for pid, info in table.items() if info["ppid"] in owned}
        additions -= owned
        if not additions:
            break
        owned.update(additions)
    for pid in owned:
        known[pid] = table[pid]["start"]
    return owned, table


def _number(value):
    try:
        return float(str(value).strip().split()[0])
    except (ValueError, IndexError):
        return None


def _xml_process_memory(inventory=None):
    """Fallback for drivers whose pmon omits per-process framebuffer memory."""
    result = subprocess.run(["nvidia-smi", "-q", "-x"], check=True,
                            capture_output=True, text=True, timeout=10)
    inventory = gpu_inventory() if inventory is None else inventory
    indices = {item["uuid"].lower(): item["index"] for item in inventory}
    memory = {}
    for gpu in ET.fromstring(result.stdout).findall("gpu"):
        index = indices.get((gpu.findtext("uuid") or "").lower())
        if index is None:
            continue
        for process in gpu.findall("processes/process_info"):
            try:
                pid = int(process.findtext("pid"))
            except (TypeError, ValueError):
                continue
            memory[(index, pid)] = _number(process.findtext("used_memory"))
    return memory


def gpu_processes(inventory=None):
    """pmon includes UE graphics jobs and activity, unlike compute-apps query."""
    result = subprocess.run(["nvidia-smi", "pmon", "-s", "um", "-c", "1"],
                            check=True, capture_output=True, text=True, timeout=10)
    processes = []
    header = None
    for line in result.stdout.splitlines():
        line = line.strip()
        if not line:
            continue
        if line.startswith("#"):
            columns = line.lstrip("#").split()
            if columns[:3] == ["gpu", "pid", "type"]:
                header = columns
            continue
        fields = line.split()
        try:
            values = dict(zip(header or [], fields))
            processes.append({"gpu_index": int(fields[0]), "pid": int(fields[1]),
                              "type": fields[2],
                              "framebuffer_mb": _number(values.get("fb")),
                              "sm_util_percent": _number(values.get("sm"))})
        except (ValueError, IndexError):
            continue
    if any(process["framebuffer_mb"] is None for process in processes):
        memory = _xml_process_memory(inventory)
        for process in processes:
            if process["framebuffer_mb"] is None:
                process["framebuffer_mb"] = memory.get((process["gpu_index"], process["pid"]))
    return processes


def placement_violations(assignments, target_index, tiny_graphics_mb=128,
                         verified_target_pids=None):
    """Allow idle display-only contexts; reject work on any protected GPU.

    Vulkan/X may create a zero/small graphics context on the display GPU while
    the *same* PID renders on the correctly selected GPU. Merely appearing in
    pmon is therefore not evidence of rendering on that display GPU.
    """
    target_pids = {item["pid"] for item in assignments if item["gpu_index"] == target_index}
    # During normal Vulkan shutdown, target resources may disappear from pmon
    # before the display-only context. The caller verifies historical PIDs with
    # /proc start times so a reused PID cannot inherit this permission.
    target_pids.update(verified_target_pids or ())
    violations = []
    for item in assignments:
        if item["gpu_index"] == target_index:
            continue
        memory = item.get("framebuffer_mb")
        activity = item.get("sm_util_percent")
        small_display_context = (item.get("type") == "G" and item["pid"] in target_pids
                                 and memory is not None and 0 <= memory <= tiny_graphics_mb
                                 and (activity is None or activity <= 0))
        if not small_display_context:
            reason = ("compute_context" if "C" in item.get("type", "") else
                      "sm_activity" if activity is not None and activity > 0 else
                      "framebuffer_allocation" if memory is not None and memory > tiny_graphics_mb else
                      "unverified_graphics_placement")
            violations.append({**item, "reason": reason})
    return violations


def terminate_owned(process, known, grace=10):
    """Terminate only verified groups descended from our new process session."""
    owned, table = _update_owned(process.pid, known)
    groups = set()
    for pid in owned:
        group = table[pid]["pgrp"]
        # A group is safe only when all currently visible members are ours.
        members = {member for member, info in table.items() if info["pgrp"] == group}
        if members and members <= owned and group != os.getpgrp():
            groups.add(group)
    for group in groups:
        try:
            os.killpg(group, signal.SIGTERM)
        except ProcessLookupError:
            pass
    deadline = time.monotonic() + grace
    while time.monotonic() < deadline:
        alive, _ = _update_owned(process.pid, known)
        if not alive:
            break
        time.sleep(0.2)
    alive, table = _update_owned(process.pid, known)
    for group in groups:
        members = {pid for pid, info in table.items() if info["pgrp"] == group}
        if members and members <= alive:
            try:
                os.killpg(group, signal.SIGKILL)
            except ProcessLookupError:
                pass
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        pass


def _mean(samples, key):
    values = [sample[key] for sample in samples if sample.get(key) is not None]
    return statistics.mean(values) if values else None


def run_benchmark(args):
    if os.name != "posix":
        raise RuntimeError("Render benchmarking requires Linux process-group isolation")
    command = args.command[1:] if args.command[:1] == ["--"] else args.command
    if not command:
        raise ValueError("Supply the rendering command after --")
    inventory = gpu_inventory()
    gpu = find_gpu(args.gpu_id, inventory)
    if not args.allow_busy_gpu:
        if (gpu["memory_used_mb"] is None or gpu["utilization_gpu_percent"] is None
                or gpu["memory_used_mb"] > args.max_initial_memory_mb
                or gpu["utilization_gpu_percent"] > args.max_initial_utilization):
            raise RuntimeError("Target GPU is busy; refusing to benchmark: " + json.dumps(gpu))
    result_dir = Path(args.result_dir).resolve()
    result_dir.mkdir(parents=True, exist_ok=True)
    label = args.label
    if not label or Path(label).name != label or label in (".", ".."):
        raise ValueError("label must be a single filename component")
    json_path = result_dir / (label + ".json")
    csv_path = result_dir / (label + ".csv")
    log_path = result_dir / (label + ".log")
    if not args.overwrite and any(path.exists() for path in (json_path, csv_path, log_path)):
        raise FileExistsError("Benchmark label already exists; choose a new label")
    output_dir = Path(args.output_dir).resolve()
    before = output_snapshot(output_dir)
    started_at = datetime.datetime.now(datetime.timezone.utc).isoformat()
    samples, warnings, placement = [], [], []
    verified_target_processes = {}
    totals = output_totals(before, before)
    known, process, failure = {}, None, None
    first_output_time = last_output_time = None
    previous_count = 0
    next_output_scan = next_guard = 0.0
    guard_available = None
    start = time.monotonic()
    try:
        with log_path.open("w", buffering=1) as log, csv_path.open("w", newline="") as csv_file:
            process = subprocess.Popen(command, stdout=log, stderr=subprocess.STDOUT,
                                       start_new_session=True)
            print(json.dumps({"event": "started", "pid": process.pid, "gpu": gpu,
                              "log": str(log_path)}), flush=True)
            csv_fields = ["elapsed_seconds", "gpu_util_percent", "memory_util_percent",
                          "gpu_memory_mb", "power_w", "sm_clock_mhz", "temperature_c",
                          "rgb_png", "depth_exr", "other_exr", "image_files", "image_bytes"]
            writer = csv.DictWriter(csv_file, fieldnames=csv_fields)
            writer.writeheader()
            while True:
                elapsed = time.monotonic() - start
                owned, _ = _update_owned(process.pid, known)
                if elapsed >= args.timeout:
                    failure = "timeout"
                    break
                if elapsed >= next_output_scan:
                    totals = output_totals(before, output_snapshot(output_dir))
                    if totals["image_files"] > previous_count:
                        first_output_time = first_output_time if first_output_time is not None else elapsed
                        last_output_time = elapsed
                    previous_count = totals["image_files"]
                    next_output_scan = elapsed + args.output_scan_interval
                try:
                    sample = gpu_sample(gpu["uuid"])
                    sample.update({"elapsed_seconds": round(elapsed, 3), **totals})
                    samples.append(sample)
                    writer.writerow(sample)
                    csv_file.flush()
                except (OSError, subprocess.SubprocessError) as exc:
                    if not warnings or warnings[-1] != str(exc):
                        warnings.append(str(exc))
                if not args.no_placement_guard and elapsed >= next_guard:
                    try:
                        assignments = gpu_processes(inventory)
                        owned, _ = _update_owned(process.pid, known)
                        ours = [item for item in assignments if item["pid"] in owned]
                        for item in ours:
                            if item["gpu_index"] == gpu["index"]:
                                verified_target_processes[item["pid"]] = known[item["pid"]]
                        verified_target_pids = {pid for pid, start_time in verified_target_processes.items()
                                                if pid in owned and known.get(pid) == start_time}
                        placement.extend({"elapsed_seconds": round(elapsed, 3), **item} for item in ours)
                        wrong = placement_violations(ours, gpu["index"], args.tiny_graphics_context_mb,
                                                     verified_target_pids)
                        guard_available = True
                        if wrong:
                            failure = "own_process_on_wrong_gpu: " + json.dumps(wrong)
                            break
                    except (OSError, subprocess.SubprocessError) as exc:
                        guard_available = False
                        warning = "GPU placement monitoring unavailable: " + str(exc)
                        if warning not in warnings:
                            warnings.append(warning)
                        failure = "gpu_placement_monitor_failed: " + str(exc)
                        break
                    next_guard = elapsed + args.placement_interval
                if process.poll() is not None:
                    break
                time.sleep(min(args.interval, max(0.01, args.timeout - (time.monotonic() - start))))
    except KeyboardInterrupt:
        failure = "interrupted"
    except Exception as exc:
        failure = "benchmark_error: " + str(exc)
    finally:
        if process is not None and (failure or process.poll() is None):
            terminate_owned(process, known)
    elapsed = time.monotonic() - start
    totals = output_totals(before, output_snapshot(output_dir))
    if totals["image_files"] > previous_count:
        first_output_time = first_output_time if first_output_time is not None else elapsed
        last_output_time = elapsed
    return_code = process.poll() if process is not None else None
    if failure is None and return_code != 0:
        failure = "command_exit_code: {}".format(return_code)
    if failure is None and not totals["image_files"] and not args.allow_no_output:
        failure = "no_images_generated"
    active_samples = [sample for sample in samples
                      if first_output_time is not None and last_output_time is not None
                      and first_output_time <= sample["elapsed_seconds"] <= last_output_time]
    gpu_memories = [sample["gpu_memory_mb"] for sample in samples
                    if sample.get("gpu_memory_mb") is not None]
    summary = {
        "label": label, "success": failure is None, "failure": failure,
        "started_at_utc": started_at, "wall_seconds": elapsed, "command": command,
        "command_exit_code": return_code, "gpu": gpu, "output_dir": str(output_dir),
        "generated": totals,
        "images_per_wall_second": totals["image_files"] / elapsed if elapsed else 0,
        "rgb_frames_per_wall_second": totals["rgb_png"] / elapsed if elapsed else 0,
        "output_mib_per_wall_second": totals["image_bytes"] / (1024 * 1024 * elapsed) if elapsed else 0,
        "gpu_util_percent_mean": _mean(samples, "gpu_util_percent"),
        "gpu_util_percent_during_output_mean": _mean(active_samples, "gpu_util_percent"),
        "gpu_memory_mb_peak": max(gpu_memories) if gpu_memories else None,
        "gpu_power_w_mean": _mean(samples, "power_w"),
        "first_output_observed_seconds": first_output_time,
        "last_output_observed_seconds": last_output_time,
        "sample_count": len(samples), "placement_guard_available": guard_available,
        "allowed_idle_graphics_context_mb": args.tiny_graphics_context_mb,
        "own_gpu_process_assignments": placement, "warnings": warnings,
        "csv_path": str(csv_path), "log_path": str(log_path),
        "measurement_notes": [
            "Wall throughput includes process startup, shader work, output encoding and shutdown.",
            "Output window is approximate, sampled every {} seconds.".format(args.output_scan_interval),
            "NVIDIA GPU utilization is sampled activity, not percentage of theoretical throughput.",
            "PNG counts assume enabled PNG passes are RGB; EXR depth is identified from output paths.",
            "Image file counts do not verify image content or dataset correctness.",
        ],
    }
    json_path.write_text(json.dumps(summary, indent=2) + "\n")
    print(json.dumps(summary, indent=2), flush=True)
    return 0 if summary["success"] else 1


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--gpu-id", default="1")
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--result-dir", required=True)
    parser.add_argument("--label", required=True)
    parser.add_argument("--timeout", type=float, default=1800)
    parser.add_argument("--interval", type=float, default=1)
    parser.add_argument("--output-scan-interval", type=float, default=3)
    parser.add_argument("--placement-interval", type=float, default=5)
    parser.add_argument("--max-initial-memory-mb", type=float, default=1024)
    parser.add_argument("--max-initial-utilization", type=float, default=10)
    parser.add_argument("--allow-busy-gpu", action="store_true")
    parser.add_argument("--no-placement-guard", action="store_true")
    parser.add_argument("--tiny-graphics-context-mb", type=float, default=128,
                        help="Allow idle pure-G display contexts only when same PID is on target GPU")
    parser.add_argument("--allow-no-output", action="store_true")
    parser.add_argument("--overwrite", action="store_true")
    parser.add_argument("command", nargs=argparse.REMAINDER)
    args = parser.parse_args()
    if min(args.timeout, args.interval, args.output_scan_interval, args.placement_interval) <= 0:
        parser.error("timeout and sampling intervals must be positive")
    raise SystemExit(run_benchmark(args))


if __name__ == "__main__":
    main()
