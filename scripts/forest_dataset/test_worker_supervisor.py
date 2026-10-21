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
if mode == "forced_zero":
    signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))
elif mode == "forced_kill":
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
if mode == "normal":
    send("Pipeline Finished")
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
import asyncio, logging, os, sys
sys.path.insert(0, sys.argv[1])
from run_cmd_async import supervise
logging.basicConfig(level=logging.INFO)
mode, port, report, child = sys.argv[2], int(sys.argv[3]), sys.argv[4], sys.argv[5]
raise SystemExit(asyncio.run(supervise(
    [sys.executable, child, mode, str(port), report],
    dict(os.environ), port, stop_grace=0.2, terminate_grace=0.2)))
"""


class SupervisorCancellationTests(unittest.TestCase):
    def run_case(self, mode, expected):
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
                if mode != "normal":
                    os.killpg(process.pid, signal.SIGINT)
                output = process.communicate(timeout=5)[0].decode()
                self.assertEqual(process.returncode, expected, output)
                if mode != "normal":
                    self.assertEqual(report.with_suffix(".command").read_bytes(), b"MatrixCity Cancel")
                if mode == "crash":
                    self.assertIn("WORKER_STOP_ABNORMAL: child exited rc=255", output)
                if mode.startswith("forced_"):
                    self.assertIn("WORKER_STOP_FORCED", output)
                else:
                    self.assertNotIn("WORKER_STOP_FORCED", output)
                with self.assertRaises(ProcessLookupError):
                    os.kill(info["pid"], 0)
            finally:
                if process.poll() is None:
                    process.kill()
                    process.communicate()
                if info:
                    try:
                        os.killpg(info["pgid"], signal.SIGKILL)
                    except ProcessLookupError:
                        pass

    def test_completed_worker_returns_zero(self):
        self.run_case("normal", 0)

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
