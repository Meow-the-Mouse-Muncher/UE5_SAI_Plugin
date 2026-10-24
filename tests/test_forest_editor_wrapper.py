"""Exercise the editor wrapper with shell shims, without UE, GPUs or user changes."""
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest


WRAPPER = Path(__file__).resolve().parents[1] / "scripts/run_forest_editor.sh"
EDITOR_FLAGS = ["-vulkan", "-RenderOffscreen", "-unattended", "-nosound", "-nosplash",
                "-stdout", "-FullStdOutLogOutput", "-ddc=InstalledNoZenLocalFallback"]


class ForestEditorWrapperTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="forest-editor-wrapper-")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.shims = self.root / "shims"
        self.shims.mkdir()
        self.executable(self.shims / "id", "#!/bin/sh\nprintf '1001\\n'\n")
        self.editor = self.root / "editor with spaces" / "UnrealEditor"
        self.editor.parent.mkdir()
        self.executable(self.editor, "#!/usr/bin/python3\n"
            "import json, os, sys\n"
            "print(json.dumps({'argv': sys.argv[1:], 'cwd': os.getcwd(), 'env': dict(os.environ)}))\n")
        self.environment = dict(os.environ)
        for name in ("XDG_CACHE_HOME", "UE_LocalDataCachePath"):
            self.environment.pop(name, None)
        self.environment.update(PATH=str(self.shims) + os.pathsep + os.defpath,
                                FOREST_EDITOR_BIN=str(self.editor))

    @staticmethod
    def executable(path, contents):
        path.write_text(contents, encoding="utf-8")
        path.chmod(0o755)

    def launch(self, arguments=(), environment=None, cwd=None):
        return subprocess.run(["bash", str(WRAPPER), *arguments],
            env=self.environment if environment is None else environment,
            cwd=cwd, capture_output=True, text=True, check=False)

    def test_preserves_external_cache_ddc_and_argument_boundaries(self):
        self.environment.update(XDG_CACHE_HOME="/cpfs/cache with spaces",
                                UE_LocalDataCachePath="/cpfs/ddc with spaces",
                                TMPDIR="/cpfs/tmp", CUDA_VISIBLE_DEVICES="GPU-test")
        arguments = ["/project with spaces/forest.uproject", "-ExecCmds=py /script with spaces/pipeline.py"]
        result = self.launch(arguments)
        self.assertEqual(result.returncode, 0, result.stderr)
        payload = json.loads(result.stdout)
        self.assertEqual(payload["argv"], arguments + EDITOR_FLAGS)
        self.assertEqual(payload["cwd"], str(self.editor.parent))
        self.assertEqual(payload["env"]["XDG_CACHE_HOME"], "/cpfs/cache with spaces")
        self.assertEqual(payload["env"]["UE_LocalDataCachePath"], "/cpfs/ddc with spaces")
        self.assertEqual(payload["env"]["TMPDIR"], "/cpfs/tmp")
        self.assertEqual(payload["env"]["CUDA_VISIBLE_DEVICES"], "GPU-test")

    def test_default_caches_and_driver_environment_match_deployed_launcher(self):
        result = self.launch()
        self.assertEqual(result.returncode, 0, result.stderr)
        environment = json.loads(result.stdout)["env"]
        self.assertEqual(environment["XDG_CACHE_HOME"], "/opt/ue56-cache")
        self.assertEqual(environment["UE_LocalDataCachePath"], "/opt/ue56-cache/ddc-render-fixed")
        self.assertEqual(environment["HOME"], "/opt/ue-home")
        self.assertEqual(environment["USER"], "ue")
        self.assertEqual(environment["LOGNAME"], "ue")
        self.assertEqual(environment["XDG_RUNTIME_DIR"], "/opt/ue-home/runtime")
        self.assertEqual(environment["VK_DRIVER_FILES"], "/opt/ue56-install/nvidia-egl.json")
        self.assertEqual(environment["VK_ICD_FILENAMES"], "/opt/ue56-install/nvidia-egl.json")
        self.assertEqual(environment["LD_LIBRARY_PATH"],
            "/usr/lib/x86_64-linux-gnu:/usr/local/nvidia/lib64:/usr/local/nvidia/lib")
        self.assertEqual(environment["PATH"], "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin")

    def test_removes_display_and_python_injection_variables(self):
        removed = ("DISPLAY", "WAYLAND_DISPLAY", "SDL_VIDEODRIVER", "PYTHONHOME", "PYTHONPATH", "LD_PRELOAD")
        self.environment.update({name: "inherited value" for name in removed})
        # A real library avoids loader noise before the wrapper can unset it.
        self.environment["LD_PRELOAD"] = "libm.so.6"
        result = self.launch()
        self.assertEqual(result.returncode, 0, result.stderr)
        environment = json.loads(result.stdout)["env"]
        for name in removed:
            self.assertNotIn(name, environment)

    def test_relative_editor_override_is_resolved_before_changing_directory(self):
        self.environment["FOREST_EDITOR_BIN"] = str(self.editor.relative_to(self.root))
        result = self.launch(cwd=self.root)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)["cwd"], str(self.editor.parent))

    def test_missing_editor_fails_without_launching(self):
        self.environment["FOREST_EDITOR_BIN"] = str(self.root / "missing")
        result = self.launch()
        self.assertEqual(result.returncode, 2)
        self.assertIn("not executable", result.stderr)
        self.assertEqual(result.stdout, "")

    def test_root_invocation_forwards_original_arguments_to_runuser(self):
        self.executable(self.shims / "id", "#!/bin/sh\nprintf '0\\n'\n")
        self.executable(self.shims / "runuser", "#!/usr/bin/python3\n"
            "import json, os, sys\n"
            "print(json.dumps({'argv': sys.argv[1:], 'editor': os.environ['FOREST_EDITOR_BIN']}))\n")
        result = self.launch(["/project with spaces/forest.uproject", "-headless"])
        self.assertEqual(result.returncode, 0, result.stderr)
        payload = json.loads(result.stdout)
        self.assertEqual(payload["argv"], ["-u", "ue", "--", str(WRAPPER),
                                          "/project with spaces/forest.uproject", "-headless"])
        self.assertEqual(payload["editor"], str(self.editor))


if __name__ == "__main__":
    unittest.main()
