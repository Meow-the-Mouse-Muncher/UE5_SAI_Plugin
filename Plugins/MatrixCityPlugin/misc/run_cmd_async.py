"""Run one UE render worker; GPU IDs refer to physical nvidia-smi devices."""
import argparse
import asyncio
import json
import logging
import os
import signal
import time
from pathlib import Path

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


async def supervise(command, env, port, timeout=0, grace=45,
                    stop_grace=45, terminate_grace=15):
    finished = asyncio.Event()
    failed = asyncio.Event()
    stop_requested = asyncio.Event()
    writers = set()
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

    server = None
    proc = None
    begin = time.monotonic()

    async def force_stop():
        nonlocal forced_stop
        if proc is None or proc.returncode is not None:
            return
        forced_stop = True
        logging.error("WORKER_STOP_FORCED: graceful stop timed out; sending SIGTERM to owned UE group")
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
            await proc.wait()

    async def stop_owned_process():
        nonlocal stopping
        if proc is None or proc.returncode is not None:
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
        completion_at = None
        while proc.returncode is None:
            if stop_requested.is_set():
                await stop_owned_process()
                # A stop request does not make a crash or forced termination
                # a normal cancellation. Preserve the child's failure status.
                if proc.returncode:
                    logging.error("WORKER_STOP_ABNORMAL: child exited rc=%s", proc.returncode)
                    return proc.returncode if proc.returncode > 0 else 128 - proc.returncode
                if forced_stop or proc.returncode is None:
                    return 1
                return stop_code
            try:
                await asyncio.wait_for(proc.wait(), timeout=0.5)
            except asyncio.TimeoutError:
                pass
            now = time.monotonic()
            if (finished.is_set() or failed.is_set()) and completion_at is None:
                completion_at = now
            if timeout and now - begin > timeout:
                logging.error("UE worker exceeded %s seconds", timeout)
                await stop_owned_process()
                return 1
            # The terminal socket marker precedes the engine's final GPU flush.
            if completion_at is not None and now - completion_at > grace:
                logging.error("Completed worker did not exit after grace; requesting editor shutdown")
                await stop_owned_process()
                # A terminal marker alone does not prove final GPU teardown.
                return 1
        if failed.is_set():
            return 1
        if not finished.is_set():
            logging.error("UE exited %s without Pipeline Finished", proc.returncode)
            return proc.returncode or 1
        return proc.returncode or 0
    finally:
        try:
            if proc is not None and proc.returncode is None:
                await stop_owned_process()
        finally:
            if server is not None:
                server.close()
                await server.wait_closed()
            for writer in list(writers):
                writer.close()
            for signum, previous in old_handlers.items():
                loop.remove_signal_handler(signum)
                signal.signal(signum, previous)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config_file", "-f", default=str(PLUGIN_ROOT / "misc/user.json"))
    parser.add_argument("--gpu_id", "-g", default=None)
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
        from gpu_devices import resolve_vulkan_adapter
        adapter, env, info = resolve_vulkan_adapter(args.gpu_id, env)
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
