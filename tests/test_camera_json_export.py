"""Protocol boundary checks without Blender, UE, image rendering, or real data."""
import copy
import hashlib
import json
from pathlib import Path
import struct
import sys

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import camera_json_protocol as protocol


def png_header(width=512, height=512):
    return b"\x89PNG\r\n\x1a\n" + struct.pack(">I", 13) + b"IHDR" + struct.pack(">II", width, height)


def exr_header(width=512, height=512, *, flags=0, include_data_window=True):
    """A standard flat OpenEXR header; pixel chunks are unnecessary for header checks."""
    def attribute(name, kind, value):
        return name.encode() + b"\0" + kind.encode() + b"\0" + struct.pack("<I", len(value)) + value

    window = struct.pack("<iiii", 0, 0, width - 1, height - 1)
    channels = b"Z\0" + struct.pack("<iB3xii", 2, 0, 1, 1) + b"\0"
    attributes = [attribute("channels", "chlist", channels), attribute("compression", "compression", b"\0"),
                  attribute("displayWindow", "box2i", window), attribute("lineOrder", "lineOrder", b"\0"),
                  attribute("pixelAspectRatio", "float", struct.pack("<f", 1)),
                  attribute("screenWindowCenter", "v2f", struct.pack("<ff", 0, 0)),
                  attribute("screenWindowWidth", "float", struct.pack("<f", 1))]
    if include_data_window:
        attributes.insert(2, attribute("dataWindow", "box2i", window))
    return struct.pack("<II", 20000630, 2 | flags) + b"".join(attributes) + b"\0"


def make_pair(root, trajectory="plane_grid", prefix="scene_010_Target_001_height_050_ang_000", fbx_root=None):
    root = Path(root)
    root.mkdir(parents=True, exist_ok=True)
    fbx_root = Path(fbx_root) if fbx_root is not None else root / "Exported_FBX"
    directory = fbx_root / trajectory
    directory.mkdir(parents=True, exist_ok=True)
    (directory / f"{prefix}.fbx").write_bytes(b"fixture source FBX")
    for suffix in ("GT", "occ"):
        directory = root / trajectory / f"{prefix}_{suffix}"
        for kind, extension in (("rgb", "png"), ("depth", "exr")):
            (directory / kind).mkdir(parents=True, exist_ok=True)
            for index in range(25):
                (directory / kind / f"{index:04d}.{extension}").write_bytes(
                    png_header() if kind == "rgb" else exr_header())
    return protocol.discover_jobs(root, fbx_root=fbx_root, trajectories=[trajectory], sequences=[prefix])[0]


def projection():
    matrix = np.zeros((4, 4))
    matrix[0, 0] = 2 * 85 / 35
    matrix[1, 1] = 2 * 85 / 35
    matrix[2, 2] = -1.02
    matrix[2, 3] = -.202
    matrix[3, 2] = -1
    return matrix


def payload(job):
    poses = [protocol.rigid_opencv_c2w(np.eye(4)) for _ in range(25)]
    return protocol.build_payload(job, poses, protocol.intrinsics_from_projection(projection()),
                                  {"blender_version": "test", "anim_offset": 0, "source_fbx_units": "cm"})


def test_discovers_new_output_layout_and_preserves_sequence_identity(tmp_path):
    job = make_pair(tmp_path / "data")
    assert job["trajectory"] == "plane_grid"
    assert Path(job["fbx_path"]).parent.parent == tmp_path / "data" / "Exported_FBX"
    assert Path(job["GT_dir"]).name == job["prefix"] + "_GT"
    assert not (Path(job["GT_dir"]) / "pose").exists()


def test_explicit_external_fbx_root_and_deterministic_filters(tmp_path):
    output = tmp_path / "data"
    fbx = tmp_path / "fbx"
    make_pair(output, "rot_arc", "b", fbx)
    make_pair(output, "fix_line", "a", fbx)
    jobs = protocol.discover_jobs(output, fbx, trajectories=["rot_arc", "fix_line"], limit=1)
    assert [(job["trajectory"], job["prefix"]) for job in jobs] == [("fix_line", "a")]
    assert protocol.discover_jobs(output, fbx, sequences="b")[0]["trajectory"] == "rot_arc"


@pytest.mark.parametrize("limit", [0, -1, 1.5, True])
def test_discovery_rejects_invalid_limits(tmp_path, limit):
    make_pair(tmp_path)
    with pytest.raises(ValueError, match="limit"):
        protocol.discover_jobs(tmp_path, limit=limit)


@pytest.mark.parametrize("prefix", ["../escape", "nested/prefix", "nested\\prefix", ".."])
def test_rejects_unsafe_sequence_filters(tmp_path, prefix):
    make_pair(tmp_path)
    with pytest.raises(ValueError, match="Unsafe|Invalid"):
        protocol.discover_jobs(tmp_path, sequences=[prefix])


def test_rejects_job_destination_override(tmp_path):
    job = make_pair(tmp_path)
    job["GT_dir"] = str(tmp_path.parent / "outside")
    with pytest.raises(ValueError, match="GT_dir disagrees"):
        protocol._job_paths(job)


def test_rejects_symlink_escape_on_output(tmp_path):
    job = make_pair(tmp_path / "data")
    outside = tmp_path / "outside"
    outside.mkdir()
    try:
        (Path(job["GT_dir"]) / "pose").symlink_to(outside, target_is_directory=True)
    except OSError:
        pytest.skip("Creating symlinks is unavailable on this host")
    with pytest.raises(ValueError, match="escapes|Symlink"):
        protocol.write_pair(job, payload(job))
    assert not list(outside.iterdir())


@pytest.mark.parametrize("scale", [0.01, 1.0, 100.0])
def test_uniform_scale_is_removed_without_changing_meter_translation(scale):
    rotation = np.array([[0., -1., 0.], [1., 0., 0.], [0., 0., 1.]])
    world = np.eye(4)
    world[:3, :3] = rotation * scale
    world[:3, 3] = [534.9, -494.2, 52.6]
    opencv = protocol.rigid_opencv_c2w(world)
    np.testing.assert_allclose(opencv[:3, :3], rotation @ np.diag([1., -1., -1.]), atol=1e-12)
    np.testing.assert_array_equal(opencv[:3, 3], world[:3, 3])
    assert np.linalg.det(opencv[:3, :3]) == pytest.approx(1)


@pytest.mark.parametrize("bad", ["shear", "nonuniform", "reflection", "singular", "nonfinite", "projective"])
def test_rejects_nonrigid_camera_world_matrix(bad):
    world = np.eye(4)
    if bad == "shear":
        world[0, 1] = 0.1
    elif bad == "nonuniform":
        world[1, 1] = 1.1
    elif bad == "reflection":
        world[0, 0] = -1
    elif bad == "singular":
        world[2, 2] = 0
    elif bad == "nonfinite":
        world[0, 3] = np.inf
    else:
        world[3, 0] = 0.1
    with pytest.raises(ValueError):
        protocol.rigid_opencv_c2w(world)


def test_projection_intrinsics_match_512_render_with_shifts():
    matrix = projection()
    matrix[0, 2], matrix[1, 2] = 0.2, -0.1
    intrinsics = protocol.intrinsics_from_projection(matrix)
    assert intrinsics["fl_x"] == pytest.approx(512 * 85 / 35)
    assert intrinsics["cx"] == pytest.approx(204.8)
    assert intrinsics["cy"] == pytest.approx(230.4)
    assert intrinsics["w"] == intrinsics["h"] == 512
    np.testing.assert_array_equal(np.array(intrinsics["intrinsics"])[2], [0, 0, 1])


def test_rejects_orthographic_or_resized_projection():
    with pytest.raises(ValueError, match="perspective"):
        protocol.intrinsics_from_projection(np.eye(4))
    with pytest.raises(ValueError, match="512x512"):
        protocol.intrinsics_from_projection(projection(), 256, 512)


@pytest.mark.parametrize("trajectory,center", [("fix_line", 12), ("plane_grid", 12), ("rot_arc", 12),
                                               ("rot_spiral", 24), ("rand_shell", 24)])
def test_payload_protocol_and_common_gt_occ_poses(tmp_path, trajectory, center):
    job = make_pair(tmp_path, trajectory)
    data = payload(job)
    assert data["schema_version"] == "camera-json-v1-25views"
    assert data["coordinate_convention"] == "opencv_c2w"
    assert data["translation_units"] == "m" and data["depth_units"] == "cm"
    assert data["pixel_center_offset"] == 0.5
    assert data["center_idx"] == center
    assert data["source"]["fbx_sha256"] == hashlib.sha256(Path(job["fbx_path"]).read_bytes()).hexdigest()
    assert [frame["frame_id"] for frame in data["frames"]] == list(range(25))
    assert data["frames"][-1]["file_path"] == "../rgb/0024.png"
    # Repeated poses are legal; retaining sequence order must not remove them.
    assert len(data["frames"]) == 25
    report = protocol.write_pair(job, data)
    left, right = map(Path, report["paths"])
    assert left.read_bytes() == right.read_bytes()
    assert report["sha256"] == hashlib.sha256(left.read_bytes()).hexdigest()
    assert protocol.write_pair(job, data)["status"] == "unchanged"


def test_no_overwrite_preflight_leaves_both_existing_files_unchanged(tmp_path):
    job = make_pair(tmp_path)
    data = payload(job)
    report = protocol.write_pair(job, data)
    paths = list(map(Path, report["paths"]))
    prior = [path.read_bytes() for path in paths]
    changed = copy.deepcopy(data)
    changed["source"]["blender_version"] = "different metadata"
    with pytest.raises(FileExistsError, match="Refusing to overwrite"):
        protocol.write_pair(job, changed)
    assert [path.read_bytes() for path in paths] == prior
    protocol.write_pair(job, changed, overwrite=True)
    assert json.loads(paths[0].read_text()) == changed
    assert paths[0].read_bytes() == paths[1].read_bytes()


def test_source_fbx_mutation_is_detected_before_writing(tmp_path):
    job = make_pair(tmp_path)
    data = payload(job)
    Path(job["fbx_path"]).write_bytes(b"changed source")
    with pytest.raises(ValueError, match="FBX changed"):
        protocol.write_pair(job, data)
    assert not (Path(job["GT_dir"]) / "pose").exists()


def test_pair_write_rolls_back_first_side_on_second_failure(tmp_path, monkeypatch):
    job = make_pair(tmp_path)
    data = payload(job)
    real_write = protocol._atomic_bytes
    calls = []

    def fail_second(path, content):
        calls.append(path)
        if len(calls) == 2:
            raise OSError("injected second-side failure")
        return real_write(path, content)

    monkeypatch.setattr(protocol, "_atomic_bytes", fail_second)
    with pytest.raises(OSError, match="second-side failure"):
        protocol.write_pair(job, data)
    for key in ("GT_dir", "occ_dir"):
        assert not (Path(job[key]) / "pose/transforms.json").exists()
        assert not list((Path(job[key]) / "pose").glob(".camera-json-*"))


def test_payload_rejects_wrong_count_axes_or_frame_identity(tmp_path):
    job = make_pair(tmp_path)
    good = payload(job)
    for mutate in (lambda p: p["frames"].pop(),
                   lambda p: p["frames"][0].update(file_path="../../secret.png"),
                   lambda p: p["frames"][0].update(frame_id=1),
                   lambda p: p.update(coordinate_convention="blender"),
                   lambda p: p["frames"][0]["transform_matrix"][0].__setitem__(0, 2)):
        bad = copy.deepcopy(good)
        mutate(bad)
        with pytest.raises(ValueError):
            protocol.write_pair(job, bad)
    assert not (Path(job["GT_dir"]) / "pose").exists()
