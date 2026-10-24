"""Real CPU subprocess checks for graceful cancellation and child failures."""
import json
import os
from pathlib import Path
import signal
import socket
import subprocess
import sys
import tempfile
import time
import unittest


MISC = Path(__file__).resolve().parents[2] / "Plugins/MatrixCityPlugin/misc"

CHILD = r"""
import json, os, signal, socket, struct, sys, time
from pathlib import Path
mode, port, report = sys.argv[1], int(sys.argv[2]), Path(sys.argv[3])
if mode in ("forced_zero", "finished_forced_zero"):
    signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))
elif mode in ("forced_kill", "forced_unreaped", "finished_ignores_term", "finished_unreaped"):
    signal.signal(signal.SIGTERM, signal.SIG_IGN)
s = socket.create_connection(("127.0.0.1", port))
report.write_text(json.dumps({"pid": os.getpid(), "pgid": os.getpgrp()}))
def send(message):
    packet = message.encode()
    s.sendall(struct.pack("<I", len(packet)) + packet)
def exact(count):
    data = b""
    while len(data) < count:
        chunk = s.recv(count - len(data))
        if not chunk:
            raise RuntimeError("Supervisor closed socket before stop")
        data += chunk
    return data
if mode == "exit_75_without_marker":
    sys.exit(75)
if mode in ("normal", "crossed_wait", "delayed_failure") or mode.startswith("finished_"):
    send("Pipeline Finished")
    if mode == "crossed_wait":
        time.sleep(0.7)
    if mode == "delayed_failure":
        send("Pipeline Failed")
    if mode == "finished_crash":
        sys.exit(3)
    if not mode.startswith("finished_"):
        sys.exit(0)
message = exact(struct.unpack("<I", exact(4))[0])
report.with_suffix(".command").write_bytes(message)
if mode == "crash":
    sys.exit(255)
if mode == "graceful":
    send("Pipeline Cancelled")
    sys.exit(0)
while True:
    time.sleep(1)
"""

RUNNER = r"""
import asyncio, logging, os, signal, sys
sys.path.insert(0, sys.argv[1])
import run_cmd_async
from run_cmd_async import supervise
logging.basicConfig(level=logging.INFO)
mode, port, report, child = sys.argv[2], int(sys.argv[3]), sys.argv[4], sys.argv[5]
if mode.endswith("_unreaped"):
    # Model a SIGKILL that is pending behind an uninterruptible driver call.
    real_killpg = os.killpg
    run_cmd_async.os.killpg = lambda pgid, sig: None if sig == signal.SIGKILL else real_killpg(pgid, sig)
if mode == "delayed_failure":
    # Keep the last packet in the reader coroutine until after the real child
    # exits. Process exit must not outrun consumption of Pipeline Failed.
    original_start_server = asyncio.start_server
    class DelayedReader:
        def __init__(self, reader):
            self.reader = reader
        async def readexactly(self, size):
            data = await self.reader.readexactly(size)
            if data == b"Pipeline Failed":
                await asyncio.sleep(0.2)
            return data
    async def delayed_start_server(callback, *args, **kwargs):
        async def client(reader, writer):
            await callback(DelayedReader(reader), writer)
        return await original_start_server(client, *args, **kwargs)
    asyncio.start_server = delayed_start_server
raise SystemExit(asyncio.run(supervise(
    [sys.executable, child, mode, str(port), report],
    dict(os.environ), port, grace=0.05 if mode == "crossed_wait" or mode.startswith("finished_") else 45,
    stop_grace=0.2, terminate_grace=0.2, kill_grace=0.2)))
"""


class SupervisorCancellationTests(unittest.TestCase):
    def run_case(self, mode, expected, logged=()):
        with tempfile.TemporaryDirectory() as directory:
            directory = Path(directory)
            child, runner = directory / "child.py", directory / "runner.py"
            child.write_text(CHILD)
            runner.write_text(RUNNER)
            report = directory / "child.json"
            with socket.socket() as listener:
                listener.bind(("127.0.0.1", 0))
                port = listener.getsockname()[1]
            process = subprocess.Popen(
                [sys.executable, str(runner), str(MISC), mode, str(port),
                 str(report), str(child)],
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                start_new_session=True)
            info = None
            try:
                deadline = time.monotonic() + 5
                while not report.exists() and process.poll() is None and time.monotonic() < deadline:
                    time.sleep(0.01)
                self.assertTrue(report.exists(), "Child did not connect to supervisor")
                info = json.loads(report.read_text())
                self.assertEqual(info["pid"], info["pgid"])
                self.assertNotEqual(info["pgid"], os.getpgid(process.pid))
                external_stop = mode in ("graceful", "crash", "forced_zero", "forced_kill", "forced_unreaped")
                if external_stop:
                    os.killpg(process.pid, signal.SIGINT)
                output = process.communicate(timeout=5)[0].decode()
                self.assertEqual(process.returncode, expected, output)
                if external_stop:
                    self.assertEqual(report.with_suffix(".command").read_bytes(), b"MatrixCity Cancel")
                else:
                    # A worker past its terminal marker cannot act on MRQ
                    # cancellation; slow teardown is terminated directly.
                    self.assertFalse(report.with_suffix(".command").exists(), output)
                if mode == "crash":
                    self.assertIn("WORKER_STOP_ABNORMAL: child exited rc=255", output)
                if "forced_" in mode or (mode.startswith("finished_") and mode != "finished_crash"):
                    self.assertIn("WORKER_STOP_FORCED", output)
                else:
                    self.assertNotIn("WORKER_STOP_FORCED", output)
                for text in logged:
                    self.assertIn(text, output)
                if not mode.endswith("_unreaped"):
                    with self.assertRaises(ProcessLookupError):
                        os.kill(info["pid"], 0)
            finally:
                # Kill the child first: it inherits the runner's stdout pipe.
                if info:
                    try:
                        os.killpg(info["pgid"], signal.SIGKILL)
                    except ProcessLookupError:
                        pass
                if process.poll() is None:
                    process.kill()
                    process.communicate()

    def test_completed_worker_returns_zero(self):
        self.run_case("normal", 0)

    def test_natural_exit_after_wait_crosses_grace_returns_zero(self):
        self.run_case("crossed_wait", 0)

    def test_final_failure_packet_is_drained_after_child_exit(self):
        self.run_case("delayed_failure", 1)

    def test_slow_teardown_after_finished_is_terminated_and_reported(self):
        self.run_case("finished_slow_exit", 75, ["WORKER_TEARDOWN_INCOMPLETE"])

    def test_completed_worker_forced_zero_exit_is_teardown_incomplete(self):
        self.run_case("finished_forced_zero", 75, ["WORKER_TEARDOWN_INCOMPLETE"])

    def test_teardown_ignoring_sigterm_is_killed(self):
        self.run_case("finished_ignores_term", 75, ["SIGTERM timed out", "WORKER_TEARDOWN_INCOMPLETE"])

    def test_unreaped_sigkill_does_not_block_supervisor(self):
        self.run_case("finished_unreaped", 76, ["WORKER_STOP_UNREAPED", "WORKER_UNREAPED"])

    def test_unreaped_child_during_external_stop_is_reported(self):
        self.run_case("forced_unreaped", 76, ["WORKER_STOP_UNREAPED", "WORKER_UNREAPED"])

    def test_crash_after_finished_is_teardown_incomplete(self):
        self.run_case("finished_crash", 75, ["WORKER_TEARDOWN_INCOMPLETE: UE exited rc=3"])

    def test_child_status_cannot_impersonate_teardown_code(self):
        self.run_case("exit_75_without_marker", 1, ["without Pipeline Finished"])

    def test_graceful_cancel_returns_requested_stop_code(self):
        self.run_case("graceful", 130)

    def test_crash_during_cancel_preserves_255(self):
        self.run_case("crash", 255)

    def test_forced_zero_exit_is_still_failure(self):
        self.run_case("forced_zero", 1)

    def test_forced_kill_preserves_signal_status(self):
        self.run_case("forced_kill", 137)


if __name__ == "__main__":
    unittest.main()
