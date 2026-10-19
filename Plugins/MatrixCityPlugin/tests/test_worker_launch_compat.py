"""Worker startup compatibility must not alter dataset samples or quality."""
import json
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest

import yaml


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "misc"))
import run_cmd_async


COMPATIBILITY_FLAGS = {
    "-Multiprocess",
    "-NoAssetRegistryCacheWrite",
    "-ini:Engine:[ConsoleVariables]:AssetRegistry.DisableDirectoryWatcher=1",
    "-ini:EditorPerProjectUserSettings:[/Script/UnrealEd.EditorLoadingSavingSettings]:bMonitorContentDirectories=False",
    "-ini:Engine:[ConsoleVariables]:r.Vulkan.Submission.AllowTimelineSemaphores=0",
}


class WorkerLaunchCompatibilityTests(unittest.TestCase):
    def fixture(self, root, worker=True):
        render = {
            "Resolution": [512, 512], "Output_Path": str(root / "images"),
            "Anti_Alias": {"enable": True, "spatial_samples": 8, "temporal_samples": 1},
            "Only_OCC": False, "Single_Frame_Only": False,
            "Render_Passes": [{"pass_name": "rgb", "enable": True, "ext": "png"},
                              {"pass_name": "depth", "enable": True, "ext": "exr"}],
        }
        render_path = root / "render.yaml"
        trajectory_path = root / "trajectory.yaml"
        worker_path = root / "worker.json"
        render_path.write_text(yaml.safe_dump(render), encoding="utf-8")
        trajectory_path.write_text(yaml.safe_dump({"global": {"num_frames": 25}}), encoding="utf-8")
        worker_path.write_text(json.dumps({"trajectory_config_path": str(trajectory_path),
                                           "trajectory_tasks": [["rot_arc", 5000, 120]]}), encoding="utf-8")
        config = {"ue_command": sys.executable, "ue_project": str(root / "project.uproject"),
                  "python_script": str(root / "pipeline.py"), "render_config": str(render_path)}
        args = SimpleNamespace(render_config=None, ue_map="/Game/Map/scene_010", port=12345,
                               worker_config=str(worker_path) if worker else None, headless=True,
                               log_file=str(root / "ue.log"), ue_arg=[])
        return config, args, (render_path, trajectory_path, worker_path)

    def test_worker_command_contains_supported_isolation_and_fence_flags(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            config, args, paths = self.fixture(root)
            before = {path: path.read_bytes() for path in paths}
            command = run_cmd_async.build_command(config, args, 1, root)
            self.assertTrue(COMPATIBILITY_FLAGS.issubset(command))
            self.assertIn("-graphicsadapter=1", command)
            self.assertIn("-render_config_path=" + str(paths[0].resolve()), command)
            self.assertFalse(any("IgnoreCPUReads" in argument for argument in command))
            self.assertFalse(any(argument.lower() == "-norhithread" for argument in command))
            self.assertEqual(before, {path: path.read_bytes() for path in paths})
            # Only watcher/queue synchronization CVars are added; no quality CVars.
            ini = {argument for argument in command if argument.startswith("-ini:")}
            self.assertEqual(ini, {flag for flag in COMPATIBILITY_FLAGS if flag.startswith("-ini:")})
            render = yaml.safe_load(paths[0].read_text(encoding="utf-8"))
            self.assertEqual(render["Resolution"], [512, 512])
            self.assertEqual(render["Anti_Alias"]["spatial_samples"], 8)
            self.assertEqual(render["Anti_Alias"]["temporal_samples"], 1)
            self.assertIs(render["Single_Frame_Only"], False)
            self.assertIs(render["Only_OCC"], False)
            self.assertEqual(yaml.safe_load(paths[1].read_text())["global"]["num_frames"], 25)
            self.assertEqual([(item["pass_name"], item["ext"]) for item in render["Render_Passes"]],
                             [("rgb", "png"), ("depth", "exr")])

    def test_compatibility_flags_are_scoped_to_worker_config_launches(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            config, args, _ = self.fixture(root, worker=False)
            command = run_cmd_async.build_command(config, args, 1, root)
            self.assertFalse(COMPATIBILITY_FLAGS.intersection(command))


if __name__ == "__main__":
    unittest.main()
