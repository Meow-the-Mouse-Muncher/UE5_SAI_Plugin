"""Check run-plan resolution and launcher isolation without starting Blender."""
import json
import os
from pathlib import Path
import struct
import sys
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import export_camera_json as launcher


def exr_header(width=512, height=512):
    def attribute(name, kind, value):
        return name.encode() + b"\0" + kind.encode() + b"\0" + struct.pack("<I", len(value)) + value

    window = struct.pack("<iiii", 0, 0, width - 1, height - 1)
    channels = b"Z\0" + struct.pack("<iB3xii", 2, 0, 1, 1) + b"\0"
    return (struct.pack("<II", 20000630, 2)
            + attribute("channels", "chlist", channels)
            + attribute("compression", "compression", b"\0")
            + attribute("dataWindow", "box2i", window)
            + attribute("displayWindow", "box2i", window)
            + attribute("lineOrder", "lineOrder", b"\0")
            + attribute("pixelAspectRatio", "float", struct.pack("<f", 1))
            + attribute("screenWindowCenter", "v2f", struct.pack("<ff", 0, 0))
            + attribute("screenWindowWidth", "float", struct.pack("<f", 1)) + b"\0")


def make_render_root(path):
    trajectory, prefix = "plane_grid", "scene_010_Target_001_height_050_ang_000"
    path = Path(path)
    fbx = path / "Exported_FBX" / trajectory
    fbx.mkdir(parents=True)
    (fbx / (prefix + ".fbx")).write_bytes(b"fixture FBX")
    header = b"\x89PNG\r\n\x1a\n" + struct.pack(">I", 13) + b"IHDR" + struct.pack(">II", 512, 512)
    for suffix in ("GT", "occ"):
        for kind, extension in (("rgb", ".png"), ("depth", ".exr")):
            directory = path / trajectory / (prefix + "_" + suffix) / kind
            directory.mkdir(parents=True)
            for index in range(25):
                (directory / f"{index:04d}{extension}").write_bytes(header if kind == "rgb" else exr_header())
    return path


def write_plan(path, initial_output_root, **updates):
    path.mkdir(parents=True)
    plan = {"output_root": str(initial_output_root), "render_config": {"Resolution": [512, 512]},
            "trajectory_config": {"global": {"num_frames": 25}}}
    plan.update(updates)
    (path / "plan.json").write_text(json.dumps(plan), encoding="utf-8")
    return path


def executable(path):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"executable fixture; never run")
    path.chmod(0o755)
    return path


def no_system_blender(monkeypatch, tmp_path):
    monkeypatch.delenv("BLENDER_BIN", raising=False)
    monkeypatch.setattr(launcher.shutil, "which", lambda _: None)
    script_dir = tmp_path / "sjy/code/UEForest/scripts"
    script_dir.mkdir(parents=True)
    monkeypatch.setattr(launcher, "SCRIPT_DIR", script_dir)
    return script_dir


def test_run_plan_selects_its_absolute_output_and_report_path(tmp_path):
    output = tmp_path / "rendered data"
    output.mkdir()
    run = write_plan(tmp_path / "run_001", output)
    root, report = launcher.resolve_run(run_dir=run)
    assert root == output.resolve()
    assert report == run / "camera_export_report.json"
    assert not report.exists()
    direct_root, direct_report = launcher.resolve_run(output_root=output)
    assert direct_root == root
    assert direct_report == root / "metadata/camera_export_report.json"


@pytest.mark.parametrize("updates,match", [
    ({"render_config": {"Resolution": [1024, 1024]}}, "native Resolution"),
    ({"trajectory_config": {"global": {"num_frames": 26}}}, "exactly 25"),
    ({"output_root": "relative/data"}, "must be absolute"),
])
def test_run_plan_rejects_incompatible_data_protocol(tmp_path, updates, match):
    output = tmp_path / "render"
    output.mkdir()
    run = write_plan(tmp_path / "run", output, **updates)
    with pytest.raises(ValueError, match=match):
        launcher.resolve_run(run_dir=run)


def test_run_resolution_never_guesses_between_two_roots(tmp_path):
    with pytest.raises(ValueError, match="exactly one"):
        launcher.resolve_run()
    with pytest.raises(ValueError, match="exactly one"):
        launcher.resolve_run(output_root=tmp_path, run_dir=tmp_path)


def test_dry_run_validates_real_render_tree_without_blender_or_writes(tmp_path, monkeypatch, capsys):
    output = make_render_root(tmp_path / "rendered data")
    no_system_blender(monkeypatch, tmp_path)
    before = {str(path.relative_to(output)): path.read_bytes()
              for path in output.rglob("*") if path.is_file()}

    def forbidden(*_, **__):
        pytest.fail("A dry-run must not start Blender or another process")

    monkeypatch.setattr(launcher.subprocess, "run", forbidden)
    assert launcher.main(["--output-root", str(output), "--trajectory", "plane_grid",
                          "--limit", "1", "--dry-run"]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["dry_run"] is True and len(result["jobs"]) == 1
    assert result["command"][0] == "blender"
    assert "--factory-startup" in result["command"]
    assert "--disable-autoexec" in result["command"]
    assert result["command"][result["command"].index("--output-root") + 1] == str(output)
    assert {str(path.relative_to(output)): path.read_bytes()
            for path in output.rglob("*") if path.is_file()} == before
    assert not (output / "metadata").exists()


def test_dry_run_still_rejects_missing_per_view_annotations(tmp_path, monkeypatch):
    output = make_render_root(tmp_path / "render")
    (next((output / "plane_grid").glob("*_GT")) / "depth/0001.exr").unlink()
    no_system_blender(monkeypatch, tmp_path)
    with pytest.raises(ValueError, match="exactly 25"):
        launcher.main(["--output-root", str(output), "--dry-run"])


def test_dry_run_rejects_non512_depth_without_a_run_plan(tmp_path, monkeypatch):
    output = make_render_root(tmp_path / "render")
    (next((output / "plane_grid").glob("*_occ")) / "depth/0001.exr").write_bytes(exr_header(256, 512))
    no_system_blender(monkeypatch, tmp_path)
    with pytest.raises(ValueError, match="Depth must be native 512x512"):
        launcher.main(["--output-root", str(output), "--dry-run"])
    assert not (output / "metadata").exists()


def test_launcher_uses_argv_not_shell_and_cleans_foreign_python_environment(tmp_path, monkeypatch):
    output = tmp_path / "render data"
    output.mkdir()
    binary = executable(tmp_path / "Blender runtime/blender")
    fbx_root = tmp_path / "FBX sources"
    fbx_root.mkdir()
    report = tmp_path / "export reports/report.json"
    calls = []
    monkeypatch.setenv("PYTHONPATH", "/unrelated/project/modules")
    monkeypatch.setenv("PYTHONHOME", "/unrelated/python")
    monkeypatch.setenv("CAMERA_EXPORT_SENTINEL", "preserved")
    monkeypatch.setattr(launcher, "discover_jobs", lambda *args: calls.append(("jobs", args)) or [{"fixture": True}])

    def launch(command, **kwargs):
        calls.append(("launch", command, kwargs))
        return SimpleNamespace(returncode=7)

    monkeypatch.setattr(launcher.subprocess, "run", launch)
    code = launcher.main(["--output-root", str(output), "--blender", str(binary),
                          "--fbx-root", str(fbx_root), "--trajectory", "rot_arc",
                          "--trajectory", "plane_grid", "--sequence", "scene_010",
                          "--camera-name", "Camera With Spaces", "--limit", "2",
                          "--report", str(report), "--overwrite"])
    assert code == 7
    _, command, kwargs = calls[-1]
    assert isinstance(command, list)
    assert command[0] == str(binary.resolve())
    assert command[command.index("--camera-name") + 1] == "Camera With Spaces"
    assert command[command.index("--report") + 1] == str(report.resolve())
    assert command[command.index("--fbx-root") + 1] == str(fbx_root.resolve())
    assert command.count("--trajectory") == 2 and command[-1] == "--overwrite"
    assert "shell" not in kwargs and kwargs["cwd"] == launcher.SCRIPT_DIR.parent
    assert "PYTHONHOME" not in kwargs["env"] and "PYTHONPATH" not in kwargs["env"]
    assert kwargs["env"]["CAMERA_EXPORT_SENTINEL"] == "preserved"
    assert os.environ["PYTHONPATH"] == "/unrelated/project/modules"
    assert calls[0][1][2:] == (["rot_arc", "plane_grid"], ["scene_010"], 2)
    assert not report.exists()


def test_explicit_blender_takes_precedence_over_environment(tmp_path, monkeypatch):
    explicit = executable(tmp_path / "explicit/blender")
    configured = executable(tmp_path / "configured/blender")
    monkeypatch.setenv("BLENDER_BIN", str(configured))
    assert launcher.blender_binary(explicit) == str(explicit.resolve())
    assert launcher.blender_binary() == str(configured.resolve())


def test_missing_blender_fails_before_starting_export(tmp_path, monkeypatch):
    no_system_blender(monkeypatch, tmp_path)
    with pytest.raises(FileNotFoundError, match="No Blender"):
        launcher.blender_binary()
    assert launcher.blender_binary(required=False) == "blender"


def test_multiple_bundled_runtimes_require_explicit_selection(tmp_path, monkeypatch):
    scripts = no_system_blender(monkeypatch, tmp_path)
    first = executable(scripts.parents[2] / "tools/blender/5.2.2/blender")
    assert launcher.blender_binary() == str(first.resolve())
    executable(scripts.parents[2] / "tools/blender/another/blender")
    with pytest.raises(ValueError, match="Multiple Blender"):
        launcher.blender_binary()


def test_project_runtime_is_preferred_to_unrelated_path_wrapper(tmp_path, monkeypatch):
    scripts = no_system_blender(monkeypatch, tmp_path)
    bundled = executable(scripts.parents[2] / "tools/blender/5.2.2/blender")
    unrelated = executable(tmp_path / "opt/legacy-run-blender")
    monkeypatch.setattr(launcher.shutil, "which", lambda _: str(unrelated))
    assert launcher.blender_binary() == str(bundled.resolve())
