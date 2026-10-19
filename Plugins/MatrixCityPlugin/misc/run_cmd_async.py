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


async def supervise(command, env, port, timeout=0, grace=45):
    finished = asyncio.Event()
    failed = asyncio.Event()
    connected = asyncio.Event()

    async def client(reader, writer):
        connected.set()
        try:
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
            writer.close()
            await writer.wait_closed()

    server = await asyncio.start_server(client, "127.0.0.1", port)
    proc = None
    begin = time.monotonic()

    async def terminate_owned_process():
        if proc is None or proc.returncode is not None:
            return
        try:
            proc.terminate()
        except ProcessLookupError:
            return
        try:
            await asyncio.wait_for(proc.wait(), timeout=15)
        except asyncio.TimeoutError:
            proc.kill()
            await proc.wait()

    try:
        proc = await asyncio.create_subprocess_exec(*command, env=env, cwd=str(PLUGIN_ROOT))
        logging.info("UE_WORKER_PID=%s", proc.pid)
        completion_at = None
        while proc.returncode is None:
            try:
                await asyncio.wait_for(proc.wait(), timeout=0.5)
            except asyncio.TimeoutError:
                pass
            now = time.monotonic()
            if (finished.is_set() or failed.is_set()) and completion_at is None:
                completion_at = now
            if timeout and now - begin > timeout:
                raise TimeoutError("UE worker exceeded {} seconds".format(timeout))
            # The terminal socket marker precedes the engine's final GPU flush.
            if completion_at is not None and now - completion_at > grace:
                logging.warning("Completed worker did not exit after grace; terminating this UE process")
                await terminate_owned_process()
                return 0 if not failed.is_set() else 1
        if failed.is_set():
            return 1
        if not finished.is_set():
            logging.error("UE exited %s without Pipeline Finished", proc.returncode)
            return proc.returncode or 1
        return proc.returncode or 0
    finally:
        server.close()
        await server.wait_closed()
        if proc is not None and proc.returncode is None:
            await terminate_owned_process()


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
