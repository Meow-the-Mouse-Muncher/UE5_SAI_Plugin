"""Scheduling, resumability and actual fragmented socket-protocol regressions."""
import asyncio
import contextlib
import io
import json
import os
from pathlib import Path
import signal
import socket
import sys
import tempfile
import threading
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "misc"))
import pipeline_config
import render_dataset
import run_cmd_async


class SchedulerTests(unittest.TestCase):
    def make_inputs(self, folder, tasks=2):
        root = Path(folder)
        project = root / "project" / "project.uproject"
        map_path = project.parent / "Content/Map/scene_010.umap"
        map_path.parent.mkdir(parents=True)
        map_path.touch()
        project.write_text("{}")
        trajectory = root / "trajectory.yaml"
        trajectory.write_text(yaml.safe_dump({"global": {
            "trajectory_types": ["fix_line", "rot_arc"][:tasks],
            "camera_heights": [5000], "plane_angles": [0], "num_frames": 25}}))
        output = root / "output"
        render = root / "render.yaml"
        render.write_text(yaml.safe_dump({"Output_Path": str(output), "Resolution": [512, 512]}))
        config = root / "user.json"
        config.write_text(json.dumps({"ue_project": str(project), "ue_command": sys.executable,
                                      "python_script": str(root / "pipeline.py"),
                                      "maps": ["scene_010"], "render_config": str(render)}))
        argv = ["--config", str(config), "--trajectory-config", str(trajectory),
                "--run-dir", str(root / "run"), "--gpu-ids", "1",
                # These fixtures exercise scheduling without camera/FBX outputs.
                "--skip-camera-json"]
        return root, project, trajectory, render, output, config, argv

    def test_recursive_dense_map_resolution_and_traversal_rejection(self):
        with tempfile.TemporaryDirectory() as folder:
            _, project, *_ = self.make_inputs(folder)
            dense = project.parent / "Content/Map/dense/scene_001.umap"
            dense.parent.mkdir()
            dense.touch()
            self.assertEqual(pipeline_config.resolve_map(project, "scene_001"),
                             "/Game/Map/dense/scene_001")
            with self.assertRaises(ValueError):
                pipeline_config.resolve_map(project, "/Game/../project")

    def test_dry_run_covers_trajectory_product_once_without_creating_outputs(self):
        with tempfile.TemporaryDirectory() as folder:
            root, _, _, _, output, _, argv = self.make_inputs(folder)
            stream = io.StringIO()
            with contextlib.redirect_stdout(stream):
                code = render_dataset.main(argv + ["--dry-run", "--workers-per-gpu", "2"])
            plan = json.loads(stream.getvalue())
            tasks = [tuple(task) for job in plan["jobs"] for task in job["trajectory_tasks"]]
            self.assertEqual(code, 0)
            self.assertEqual(len(tasks), 2)
            self.assertEqual(len(set(tasks)), 2)
            self.assertEqual(len({job["id"] for job in plan["jobs"]}), len(plan["jobs"]))
            self.assertFalse(output.exists())
            self.assertFalse((root / "run").exists())

    def test_output_inventory_counts_only_own_task_and_target(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            for target, angle, suffix in [("Target_001", 0, "occ"), ("Target_001", 0, "GT"),
                                           ("Target_002", 0, "occ"), ("Target_001", 120, "occ")]:
                directory = root / "rot_arc" / ("scene_010_{}_height_050_ang_{:03d}_{}".format(target, angle, suffix))
                for channel, extension in [("rgb", "png"), ("depth", "exr")]:
                    (directory / channel).mkdir(parents=True)
                    (directory / channel / ("0000." + extension)).write_bytes(b"image")
            job = {"map_package_path": "/Game/Map/scene_010", "target_names": ["Target_001"],
                   "trajectory_tasks": [["rot_arc", 5000, 0]]}
            count = render_dataset.output_inventory(root, job)
            self.assertEqual(count["image_files"], 4)
            self.assertEqual(count["frame_views"], 2)
            self.assertEqual(count["bytes"], 20)

    def test_launch_failure_cannot_be_reported_as_success(self):
        with tempfile.TemporaryDirectory() as folder:
            root, _, _, _, _, _, argv = self.make_inputs(folder, tasks=1)
            with patch.object(render_dataset, "choose_gpus", return_value=[{"index": 1, "uuid": "test"}]), \
                    patch.object(render_dataset.subprocess, "Popen", side_effect=OSError("test launch failure")), \
                    patch.object(threading, "excepthook", lambda args: None), \
                    contextlib.redirect_stdout(io.StringIO()):
                code = render_dataset.main(argv)
            self.assertNotEqual(code, 0)
            summary = json.loads((root / "run/result.json").read_text())
            self.assertFalse(summary["success"])

    def test_resume_skips_completed_matching_job_and_rejects_changed_config(self):
        with tempfile.TemporaryDirectory() as folder:
            root, _, _, render, output, _, argv = self.make_inputs(folder, tasks=1)
            def fake_launch(*args, **kwargs):
                directory = output / "fix_line/scene_010_Target_001_height_050_ang_000_occ/rgb"
                directory.mkdir(parents=True, exist_ok=True)
                (directory / "0000.png").write_bytes(b"image")
                return SimpleNamespace(wait=lambda: 0, poll=lambda: 0)
            with patch.object(render_dataset, "choose_gpus", return_value=[{"index": 1, "uuid": "test"}]), \
                    patch.object(render_dataset.subprocess, "Popen", side_effect=fake_launch), \
                    contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(render_dataset.main(argv), 0)
            with patch.object(render_dataset, "choose_gpus", return_value=[{"index": 1, "uuid": "test"}]), \
                    patch.object(render_dataset.subprocess, "Popen", side_effect=AssertionError("resume must skip")) as launch, \
                    contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(render_dataset.main(argv + ["--resume"]), 0)
                launch.assert_not_called()
                # Resume restores the recorded recipe by default; an explicit
                # override must still reject a different image configuration.
                render.write_text(yaml.safe_dump({"Output_Path": str(output), "Resolution": [1024, 1024]}))
                with self.assertRaises(ValueError):
                    render_dataset.main(argv + ["--resume", "--render-config", str(render)])

    def test_launcher_dry_run_does_not_create_output_directory(self):
        with tempfile.TemporaryDirectory() as folder:
            _, _, _, _, output, config, _ = self.make_inputs(folder)
            self.assertEqual(run_cmd_async.main(["--config_file", str(config), "--dry-run"]), 0)
            self.assertFalse(output.exists())

    def test_fallback_probes_are_serialized_by_shared_lock(self):
        """A failed shared probe must not turn into one local probe per worker at once."""
        import gpu_devices
        state = {"active": 0, "peak": 0}
        guard = threading.Lock()

        def probe(gpu_id, env, timeout, attempts):
            with guard:
                state["active"] += 1
                state["peak"] = max(state["peak"], state["active"])
            time.sleep(0.05)
            with guard:
                state["active"] -= 1
            return gpu_id, env, {"uuid": gpu_id}

        with tempfile.TemporaryDirectory() as folder, \
                patch.object(gpu_devices, "resolve_vulkan_adapter", probe):
            lock = str(Path(folder) / "vulkan_probe.lock")
            threads = [threading.Thread(target=run_cmd_async.resolve_gpu_binding,
                                        args=("GPU-{}".format(i), None, {}, 1, 1, lock)) for i in range(6)]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join(timeout=5)
        self.assertEqual(state["peak"], 1)

    def test_interrupt_during_process_registration_cancels_new_child(self):
        """Ctrl-C must cover children whose Popen returned after active was read."""
        with tempfile.TemporaryDirectory() as folder:
            _, _, _, _, _, _, argv = self.make_inputs(folder, tasks=1)
            entered = threading.Event()
            release = threading.Event()
            stopped = threading.Event()
            signals = []
            first_join = [True]
            threads = []
            original_join = threading.Thread.join

            def send_signal(value):
                signals.append(value)
                stopped.set()

            def wait(timeout=None):
                if not stopped.wait(timeout=3 if timeout is None else timeout):
                    raise TimeoutError("Fake child was left running")
                return -2

            fake = SimpleNamespace(wait=wait, poll=lambda: -2 if stopped.is_set() else None,
                                   send_signal=send_signal, kill=lambda: stopped.set())

            def launch(*args, **kwargs):
                entered.set()
                release.wait(timeout=3)
                return fake

            def join(thread, timeout=None):
                if thread.name.startswith("ThreadPoolExecutor"):
                    # Resume-validation pool shutdown, not the worker loop.
                    return original_join(thread, timeout)
                threads.append(thread)
                if first_join[0]:
                    first_join[0] = False
                    self.assertTrue(entered.wait(timeout=2))
                    # Exercise the installed signal handler: it records a stop
                    # without raising through Thread.join(). Popen returns
                    # after the main loop has requested worker cancellation.
                    threading.Timer(0.05, release.set).start()
                    signal.getsignal(signal.SIGINT)(signal.SIGINT, None)
                    return
                return original_join(thread, timeout=1)

            try:
                with patch.object(render_dataset, "choose_gpus", return_value=[{"index": 1, "uuid": "test"}]), \
                        patch.object(render_dataset.subprocess, "Popen", side_effect=launch), \
                        patch.object(threading.Thread, "join", join), \
                        contextlib.redirect_stdout(io.StringIO()):
                    self.assertEqual(render_dataset.main(argv), 130)
                self.assertIn(signal.SIGINT, signals)
            finally:
                release.set()
                stopped.set()
                for thread in threads:
                    original_join(thread, timeout=2)


class SocketProtocolTests(unittest.TestCase):
    def run_fake_worker(self, messages, post_delay=0):
        with socket.socket() as reserve:
            reserve.bind(("127.0.0.1", 0))
            port = reserve.getsockname()[1]
        code = "\n".join([
            "import socket, time",
            "connection = socket.create_connection(('127.0.0.1', {}))".format(port),
            "messages = " + repr(messages),
            "for message in messages:",
            "    payload = message.encode()",
            "    framed = len(payload).to_bytes(4, 'little') + payload",
            "    for value in framed:",
            "        connection.sendall(bytes([value]))",
            "        time.sleep(0.0005)",
            "time.sleep({})".format(post_delay),
            "connection.close()",
        ])
        begin = time.monotonic()
        result = asyncio.run(run_cmd_async.supervise([sys.executable, "-c", code],
                                                     dict(os.environ), port, timeout=5, grace=1))
        return result, time.monotonic() - begin

    def test_fragmented_frames_ignore_intermediate_queue_completion(self):
        result, elapsed = self.run_fake_worker(
            ["Harmless log: no error occurred", "Render completed. Success: True", "Pipeline Finished"],
            post_delay=0.2)
        self.assertEqual(result, 0)
        self.assertGreaterEqual(elapsed, 0.2)

    def test_exit_without_final_marker_is_failure(self):
        result, _ = self.run_fake_worker(["Render completed. Success: True"])
        self.assertNotEqual(result, 0)

    def test_explicit_failure_marker_is_failure(self):
        result, _ = self.run_fake_worker(["Pipeline Failed"])
        self.assertNotEqual(result, 0)


if __name__ == "__main__":
    unittest.main()
