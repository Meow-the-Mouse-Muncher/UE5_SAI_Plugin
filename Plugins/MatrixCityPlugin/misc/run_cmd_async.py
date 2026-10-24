"""Run one UE render worker; GPU IDs refer to physical nvidia-smi devices."""
import argparse
import asyncio
import contextlib
import fcntl
import json
import logging
import os
import signal
import sys
import time
from pathlib import Path

if __name__ == '__main__':
    sys.dont_write_bytecode = True

from pipeline_config import PLUGIN_ROOT, load_render_config, resolve_path


def completion_message(message):
    """Per-queue completion and log lines containing 'error' are not terminal."""
    stripped = message.strip()
    if stripped == "Pipeline Finished":
        return "finished"
    if stripped == "Pipeline Failed":
        return "failed"
    return None


def build_command(config, args, adapter_index, config_dir=None):
    project = resolve_path(config["ue_project"], config_dir)
    script = resolve_path(config["python_script"], config_dir)
    render = resolve_path(args.render_config or config["render_config"], config_dir)
    level = args.ue_map or config.get("ue_map", "")
    command = [str(resolve_path(config["ue_command"], config_dir)), str(project)]
    if level:
        command.append(level)
    command += ["-ExecCmds=py " + str(script), "-render_config_path=" + str(render),
                "-socket_port=" + str(args.port), "-notexturestreaming", "-LOCALLOGTIMES",
                "-NoVSync", "-unattended", "-nosound", "-NoSplash"]
    if level:
        command.append("-target_map=" + level)
    if adapter_index is not None:
        command.append("-graphicsadapter=" + str(adapter_index))
    if args.worker_config:
        command.append("-worker_config_path=" + str(Path(args.worker_config).resolve()))
        # UE explicitly forbids shared registry/config writes in multiprocess
        # mode. Workers still read the common cache and save their own assets.
        command.extend([
            "-Multiprocess", "-NoAssetRegistryCacheWrite",
            # UE 5.6.1 starts TraceWorker before disabling/clearing LLM.
            # Its startup logging can access a freed FLLMTracker (GetState).
            # Prevent TraceWorker and Insights auto-connect during that early
            # initialization. These flags affect profiling only.
            "-notracethreading", "-traceautostart=0",
            "-ini:Engine:[ConsoleVariables]:AssetRegistry.DisableDirectoryWatcher=1",
            "-ini:EditorPerProjectUserSettings:[/Script/UnrealEd.EditorLoadingSavingSettings]:bMonitorContentDirectories=False",
            # UE 5.6/NVIDIA: depth CPU readback can stall waiting for a timeline
            # completion task. The ordinary-fence path preserves pixel values.
            "-ini:Engine:[ConsoleVariables]:r.Vulkan.Submission.AllowTimelineSemaphores=0",
        ])
    if args.headless:
        command.append("-RenderOffScreen")
    if args.log_file:
        log_path = Path(args.log_file).resolve()
        command.append("-Abslog=" + str(log_path))
    else:
        command.append("LOG=Pipeline.log")
    command.extend(args.ue_arg)
    return command


# UE sent Pipeline Finished (no Pipeline Failed), then did not exit cleanly:
# forced after the exit grace, or a nonzero status during teardown. Renders
# and FBX export precede that marker; the scheduler decides by validating
# the outputs. Child statuses are remapped so they cannot collide with it.
EXIT_TEARDOWN_INCOMPLETE = 75
# UE was still alive after SIGKILL and its grace: the kill is pending behind an
# uninterruptible driver call and the process still holds GPU memory and
# driver state. Takes precedence over every other status; the scheduler must
# not start another UE in this slot.
EXIT_WORKER_UNREAPED = 76
SUPERVISOR_STATUSES = (EXIT_TEARDOWN_INCOMPLETE, EXIT_WORKER_UNREAPED)


def child_status(returncode):
    """Shell-style status of an exited child, never a supervisor status."""
    status = 128 - returncode if returncode < 0 else returncode
    return 1 if status in SUPERVISOR_STATUSES else status


async def supervise(command, env, port, timeout=0, grace=45,
                    stop_grace=45, terminate_grace=15, kill_grace=30):
    finished = asyncio.Event()
    failed = asyncio.Event()
    stop_requested = asyncio.Event()
    writers = set()
    readers = set()
    stop_code = 130
    stop_packet = b"MatrixCity Cancel"
    stop_packet = len(stop_packet).to_bytes(4, "little") + stop_packet
    loop = asyncio.get_running_loop()
    old_handlers = {}

    def request_stop(signum):
        nonlocal stop_code
        if not stop_requested.is_set():
            stop_code = 128 + signum
            logging.warning("WORKER_STOP_REQUEST signal=%s", signal.Signals(signum).name)
            stop_requested.set()

    # Repeated signals must not raise a second KeyboardInterrupt in cleanup.
    for signum in (signal.SIGINT, signal.SIGTERM):
        old_handlers[signum] = signal.getsignal(signum)
        loop.add_signal_handler(signum, request_stop, signum)

    stopping = False
    forced_stop = False

    def send_stop(writer):
        if not writer.is_closing():
            writer.write(stop_packet)

    async def client(reader, writer):
        task = asyncio.current_task()
        readers.add(task)
        writers.add(writer)
        try:
            if stopping:
                send_stop(writer)
                await writer.drain()
            while True:
                length = int.from_bytes(await reader.readexactly(4), "little")
                if not 0 < length <= 16 * 1024 * 1024:
                    raise ValueError("Invalid UE socket message size")
                message = (await reader.readexactly(length)).decode("utf-8", "replace")
                logging.info("UE: %s", message)
                terminal = completion_message(message)
                if terminal == "finished":
                    finished.set()
                elif terminal == "failed":
                    failed.set()
        except asyncio.IncompleteReadError:
            pass
        except Exception:
            logging.exception("Worker socket read failed")
        finally:
            writers.discard(writer)
            writer.close()
            try:
                await writer.wait_closed()
            except (ConnectionError, OSError):
                pass
            finally:
                readers.discard(task)

    server = None
    proc = None
    begin = time.monotonic()

    async def force_stop(reason="graceful stop timed out"):
        nonlocal forced_stop
        if proc is None or proc.returncode is not None:
            return
        forced_stop = True
        logging.error("WORKER_STOP_FORCED: %s; sending SIGTERM to owned UE group", reason)
        try:
            os.killpg(proc.pid, signal.SIGTERM)
        except ProcessLookupError:
            return
        try:
            await asyncio.wait_for(proc.wait(), timeout=terminate_grace)
        except asyncio.TimeoutError:
            logging.error("WORKER_STOP_FORCED: SIGTERM timed out; sending SIGKILL to owned UE group")
            try:
                os.killpg(proc.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            try:
                # SIGKILL takes effect only when an uninterruptible driver
                # call (e.g. an NVIDIA RM free) returns. Do not wait forever:
                # the scheduler's stop deadline must stay bounded.
                await asyncio.wait_for(proc.wait(), timeout=kill_grace)
            except asyncio.TimeoutError:
                logging.error("WORKER_STOP_UNREAPED: UE pid=%s still alive %ss after SIGKILL",
                              proc.pid, kill_grace)

    async def stop_owned_process():
        nonlocal stopping
        if proc is None or proc.returncode is not None or forced_stop:
            return
        stopping = True
        logging.warning("WORKER_STOP_GRACEFUL: requesting MRQ cancellation over worker socket")
        for writer in list(writers):
            try:
                send_stop(writer)
                await writer.drain()
            except (ConnectionError, OSError):
                pass
        try:
            # The listener stays open: a worker still loading its map can
            # connect during this grace period and receive the stop command.
            await asyncio.wait_for(proc.wait(), timeout=stop_grace)
            logging.warning("WORKER_STOP_EXIT: UE exited rc=%s after cancellation request", proc.returncode)
        except asyncio.TimeoutError:
            await force_stop()

    try:
        server = await asyncio.start_server(client, "127.0.0.1", port)
        # A scheduler/terminal process-group SIGINT must not reach Unreal's Unix
        # handler directly; that bypasses CloseEditor/EndPlayMap.
        proc = await asyncio.create_subprocess_exec(
            *command, env=env, cwd=str(PLUGIN_ROOT), start_new_session=True)
        logging.info("UE_WORKER_PID=%s", proc.pid)

        def unreaped():
            logging.error("WORKER_UNREAPED: UE pid=%s is still alive; its slot must not be reused", proc.pid)
            return EXIT_WORKER_UNREAPED

        completion_at = None
        while proc.returncode is None:
            if stop_requested.is_set():
                await stop_owned_process()
                if proc.returncode is None:
                    return unreaped()
                # A stop request does not make a crash or forced termination
                # a normal cancellation. Preserve the child's failure status.
                if proc.returncode:
                    logging.error("WORKER_STOP_ABNORMAL: child exited rc=%s", proc.returncode)
                    return child_status(proc.returncode)
                return 1 if forced_stop else stop_code
            try:
                await asyncio.wait_for(proc.wait(), timeout=0.5)
            except asyncio.TimeoutError:
                pass
            # wait() can return after a deadline while the child has already
            # exited normally. Its exit status takes precedence over timers.
            if proc.returncode is not None:
                break
            now = time.monotonic()
            if (finished.is_set() or failed.is_set()) and completion_at is None:
                completion_at = now
            if timeout and now - begin > timeout:
                logging.error("UE worker exceeded %s seconds", timeout)
                await stop_owned_process()
                return unreaped() if proc.returncode is None else 1
            # The terminal socket marker precedes the engine's final GPU flush
            # and quit_editor. Past that point MRQ cancellation cannot help: a
            # main thread blocked in Vulkan/driver teardown never ticks the
            # socket reader, so terminate directly instead of waiting again.
            if completion_at is not None and now - completion_at > grace:
                await force_stop("worker did not exit {:g}s after its terminal marker".format(grace))
                # Classify the actual exit below. A slow natural exit is not
                # a failed render.
                break
        if proc.returncode is None:
            return unreaped()
        # Child exit and socket EOF are independent asyncio callbacks. Consume
        # the final packets before deciding whether Finished was followed by
        # Failed, but never let an inherited/stuck socket hold the worker open.
        if readers:
            _, pending_readers = await asyncio.wait(tuple(readers), timeout=1.0)
            if pending_readers:
                logging.error("Worker socket did not close within 1 second of UE exit")
                return 1
        if stop_requested.is_set():
            if proc.returncode:
                logging.error("WORKER_STOP_ABNORMAL: child exited rc=%s", proc.returncode)
                return child_status(proc.returncode)
            return 1 if forced_stop else stop_code
        if failed.is_set():
            return 1
        if finished.is_set() and (forced_stop or proc.returncode):
            logging.error("WORKER_TEARDOWN_INCOMPLETE: UE %s after Pipeline Finished",
                          "was forced to exit" if forced_stop else "exited rc={}".format(proc.returncode))
            return EXIT_TEARDOWN_INCOMPLETE
        if forced_stop:
            return 1
        if not finished.is_set():
            logging.error("UE exited %s without Pipeline Finished", proc.returncode)
            return child_status(proc.returncode) or 1
        return 0
    finally:
        try:
            if proc is not None and proc.returncode is None:
                await stop_owned_process()
        finally:
            if server is not None:
                server.close()
            for writer in list(writers):
                writer.close()
            if server is not None:
                await server.wait_closed()
            for signum, previous in old_handlers.items():
                loop.remove_signal_handler(signum)
                signal.signal(signum, previous)


def resolve_gpu_binding(gpu_id, device_map, env, timeout=30, attempts=3, probe_lock=None):
    """Bind this worker to its Vulkan adapter, preferring the scheduler's map.

    The scheduler probes once per run because a launch storm of per-worker
    probes can exceed the probe timeout and abort the whole dataset. A map
    that no longer matches the current driver/loader environment is not an
    error: log it and probe locally, since only this worker is affected.
    When the shared probe failed, every worker falls back at once; the
    scheduler's ``probe_lock`` file serializes those local probes.
    """
    from gpu_devices import DeviceMapStale, binding_from_map, load_device_map, resolve_vulkan_adapter

    if device_map:
        try:
            return binding_from_map(load_device_map(device_map), gpu_id, env)
        except DeviceMapStale as exc:
            logging.warning("DEVICE_MAP_STALE %s; probing locally", exc)
        except (OSError, ValueError) as exc:
            logging.warning("DEVICE_MAP_UNUSABLE %s; probing locally", exc)
    with contextlib.ExitStack() as stack:
        if probe_lock:
            lock = stack.enter_context(open(probe_lock, "a"))
            waited = time.monotonic()
            try:
                fcntl.flock(lock, fcntl.LOCK_EX)
                logging.info("PROBE_LOCK acquired after %.1fs", time.monotonic() - waited)
            except OSError as exc:
                logging.warning("PROBE_LOCK unavailable (%s); probing unserialized", exc)
        adapter, launch_env, info = resolve_vulkan_adapter(gpu_id, env, timeout, attempts)
    info["source"] = "probe"
    return adapter, launch_env, info


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config_file", "-f", default=str(PLUGIN_ROOT / "misc/user.json"))
    parser.add_argument("--gpu_id", "-g", default=None)
    parser.add_argument("--gpu-uuid", default=None,
                        help="Physical NVIDIA UUID matching the device-map entry; preferred over --gpu_id for map lookup")
    parser.add_argument("--device-map", help="Scheduler device map; avoids a per-worker Vulkan probe")
    parser.add_argument("--probe-timeout", type=float, default=30)
    parser.add_argument("--probe-attempts", type=int, default=3)
    parser.add_argument("--probe-lock", help="Shared lock file that serializes fallback Vulkan probes")
    parser.add_argument("--port", "-p", type=int, default=9999)
    parser.add_argument("--maps", "-m", nargs="*", help="One full map path or name; use run_parallel.py for batches")
    parser.add_argument("--ue-map")
    parser.add_argument("--render-config")
    parser.add_argument("--worker-config")
    parser.add_argument("--headless", action="store_true")
    parser.add_argument("--log-file")
    parser.add_argument("--timeout", type=float, default=0)
    parser.add_argument("--exit-grace", type=float, default=45)
    parser.add_argument("--ue-arg", action="append", default=[])
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    config_path = resolve_path(args.config_file)
    config = json.loads(config_path.read_text(encoding="utf-8"))
    if config.get('runtime_root') or os.environ.get('FOREST_RUNTIME_ROOT'):
        from render_dataset import configure_runtime
        configure_runtime(config, argv, entry_point=__file__, config_dir=config_path.parent)
    if args.maps:
        if len(args.maps) != 1:
            parser.error("Use run_parallel.py to render multiple maps")
        from pipeline_config import resolve_map
        args.ue_map = resolve_map(resolve_path(config["ue_project"], config_path.parent), args.maps[0])
    render_path = resolve_path(args.render_config or config["render_config"], config_path.parent)
    render_config = load_render_config(render_path)
    env = dict(os.environ)
    adapter = None
    if args.gpu_id is not None:
        # The scheduler passes the physical UUID separately: a bare index in
        # --gpu_id must not be mistaken for a device-map key.
        identity = args.gpu_uuid or args.gpu_id
        adapter, env, info = resolve_gpu_binding(
            identity, args.device_map, env, args.probe_timeout, args.probe_attempts, args.probe_lock)
        env["CUDA_VISIBLE_DEVICES"] = info["uuid"]
        logging.info("GPU_BINDING %s", json.dumps(info, sort_keys=True))
    command = build_command(config, args, adapter, config_path.parent)
    logging.info("UE_COMMAND_ARGV %s", json.dumps(command))
    if args.dry_run:
        return 0
    Path(render_config["Output_Path"]).mkdir(parents=True, exist_ok=True)
    if args.log_file:
        Path(args.log_file).resolve().parent.mkdir(parents=True, exist_ok=True)
    return asyncio.run(supervise(command, env, args.port, args.timeout, args.exit_grace))


if __name__ == "__main__":
    raise SystemExit(main())
