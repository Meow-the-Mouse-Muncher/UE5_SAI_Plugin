"""Validated camera JSON export helpers; usable without Blender or Unreal."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import struct
import tempfile
from collections.abc import Mapping, Sequence

import numpy as np

SCHEMA_VERSION = "camera-json-v1-25views"
TRAJECTORIES = ("fix_line", "plane_grid", "rot_arc", "rot_spiral", "rand_shell")
FRAME_STEMS = tuple(f"{index:04d}" for index in range(25))


def _name(value: str, label: str) -> str:
    if not isinstance(value, str) or not value or value in (".", ".."):
        raise ValueError(f"Invalid {label}: {value!r}")
    if any(character in value for character in ("/", "\\", "\0")):
        raise ValueError(f"Unsafe {label}: {value!r}")
    return value


def _under(root: Path, *parts: str) -> Path:
    candidate = root.joinpath(*parts)
    resolved = candidate.resolve()
    if not resolved.is_relative_to(root):
        raise ValueError(f"Path escapes its declared root: {candidate}")
    current = root
    for part in candidate.relative_to(root).parts:
        current = current / part
        if current.is_symlink():
            raise ValueError(f"Symlink is not accepted in an export path: {current}")
    return candidate


def _job_paths(job: Mapping) -> tuple[Path, Path, Path, Path]:
    root = Path(job["output_root"]).expanduser().resolve()
    trajectory = _name(job["trajectory"], "trajectory")
    if trajectory not in TRAJECTORIES:
        raise ValueError(f"Unsupported trajectory: {trajectory}")
    prefix = _name(job["prefix"], "sequence prefix")
    expected = [_under(root, trajectory, f"{prefix}_{suffix}") for suffix in ("GT", "occ")]
    for key, path in zip(("GT_dir", "occ_dir"), expected):
        if Path(job[key]).absolute() != path:
            raise ValueError(f"{key} disagrees with output_root/trajectory/prefix")
    fbx_root = Path(job.get("fbx_root", root / "Exported_FBX")).expanduser().resolve()
    fbx = _under(fbx_root, trajectory, prefix + ".fbx")
    if Path(job["fbx_path"]).absolute() != fbx:
        raise ValueError("fbx_path disagrees with its declared FBX root")
    if not root.is_dir() or not fbx.is_file():
        raise ValueError(f"Missing output root or source FBX: {root}, {fbx}")
    return root, expected[0], expected[1], fbx


def _png_size(path: Path) -> tuple[int, int]:
    with path.open("rb") as stream:
        header = stream.read(24)
    if len(header) != 24 or header[:8] != b"\x89PNG\r\n\x1a\n" or header[12:16] != b"IHDR":
        raise ValueError(f"Invalid PNG header: {path}")
    if struct.unpack(">I", header[8:12])[0] != 13:
        raise ValueError(f"Invalid PNG IHDR length: {path}")
    return struct.unpack(">II", header[16:24])


def _exr_size(path: Path) -> tuple[int, int]:
    """Read a bounded, single-part OpenEXR header; never decode image pixels."""
    header_limit = 1024 * 1024
    file_size = path.stat().st_size
    with path.open("rb") as stream:
        def read_exact(length):
            if length < 0 or stream.tell() + length > min(file_size, header_limit):
                raise ValueError(f"Invalid or oversized OpenEXR header: {path}")
            value = stream.read(length)
            if len(value) != length:
                raise ValueError(f"Truncated OpenEXR header: {path}")
            return value

        magic, version = struct.unpack("<II", read_exact(8))
        if magic != 20000630 or version & 0xFF != 2:
            raise ValueError(f"Invalid OpenEXR magic/version: {path}")
        if version & 0x1000:
            raise ValueError(f"Multipart OpenEXR depth is unsupported: {path}")
        if version & 0x800:
            raise ValueError(f"Deep OpenEXR depth is unsupported: {path}")
        if version & ~(0xFF | 0x200 | 0x400):
            raise ValueError(f"Unknown OpenEXR version flags: {path}")
        max_name_length = 255 if version & 0x400 else 31

        def c_string():
            result = bytearray()
            while True:
                character = read_exact(1)
                if character == b"\0":
                    return bytes(result)
                result.extend(character)
                if len(result) > max_name_length:
                    raise ValueError(f"Invalid OpenEXR attribute name/type: {path}")

        dimensions = None
        names = set()
        while True:
            name = c_string()
            if not name:
                break
            if name in names:
                raise ValueError(f"Duplicate OpenEXR header attribute: {path}")
            names.add(name)
            attribute_type = c_string()
            if not attribute_type:
                raise ValueError(f"Empty OpenEXR attribute type: {path}")
            length = struct.unpack("<I", read_exact(4))[0]
            if stream.tell() + length > min(file_size, header_limit):
                raise ValueError(f"Invalid or oversized OpenEXR header attribute: {path}")
            if name == b"dataWindow":
                if attribute_type != b"box2i" or length != 16:
                    raise ValueError(f"Invalid OpenEXR dataWindow: {path}")
                minimum_x, minimum_y, maximum_x, maximum_y = struct.unpack("<iiii", read_exact(16))
                width, height = maximum_x - minimum_x + 1, maximum_y - minimum_y + 1
                if width <= 0 or height <= 0:
                    raise ValueError(f"Invalid OpenEXR dataWindow dimensions: {path}")
                dimensions = (width, height)
            else:
                stream.seek(length, os.SEEK_CUR)
        if dimensions is None:
            raise ValueError(f"OpenEXR header is missing dataWindow: {path}")
        return dimensions


def validate_render_pair(job: Mapping) -> None:
    """Require exactly 25 native 512-square RGB and per-view GT/OCC depth files."""
    root, gt, occ, _ = _job_paths(job)
    expected = set(FRAME_STEMS)
    for render_dir in (gt, occ):
        for kind, extension in (("rgb", ".png"), ("depth", ".exr")):
            directory = _under(root, *render_dir.relative_to(root).parts, kind)
            if not directory.is_dir():
                raise ValueError(f"Missing render directory: {directory}")
            files = [path for path in directory.iterdir() if path.suffix.lower() == extension]
            stems = {path.stem for path in files}
            if len(files) != 25 or stems != expected:
                raise ValueError(f"Expected exactly 25 {kind} frames 0000..0024 in {directory}")
            for path in files:
                _under(root, *path.relative_to(root).parts)
                if not path.is_file() or path.stat().st_size == 0:
                    raise ValueError(f"Missing or empty render file: {path}")
                if kind == "rgb" and _png_size(path) != (512, 512):
                    raise ValueError(f"RGB must be native 512x512: {path}")
                if kind == "depth" and _exr_size(path) != (512, 512):
                    raise ValueError(f"Depth must be native 512x512: {path}")


def discover_jobs(output_root, fbx_root=None, trajectories=None, sequences=None, limit=None) -> list[dict]:
    """Discover the new <output>/Exported_FBX/<trajectory>/<prefix>.fbx layout."""
    root = Path(output_root).expanduser().resolve()
    if not root.is_dir():
        raise ValueError(f"Output root does not exist: {root}")
    fbx_base = Path(fbx_root).expanduser().resolve() if fbx_root is not None else root / "Exported_FBX"
    if not fbx_base.is_dir():
        raise ValueError(f"FBX root does not exist: {fbx_base}")
    if isinstance(trajectories, str):
        trajectories = [trajectories]
    selected = set(TRAJECTORIES if trajectories is None else trajectories)
    if not selected or selected - set(TRAJECTORIES):
        raise ValueError(f"Unsupported trajectory selection: {sorted(selected)}")
    if isinstance(sequences, str):
        sequences = [sequences]
    prefixes = None if sequences is None else {_name(value, "sequence prefix") for value in sequences}
    if limit is not None and (isinstance(limit, bool) or not isinstance(limit, int) or limit < 1):
        raise ValueError("limit must be a positive integer")
    jobs = []
    for trajectory in sorted(selected):
        directory = _under(fbx_base, trajectory)
        if not directory.exists():
            continue
        for fbx in sorted(directory.glob("*.fbx")):
            prefix = _name(fbx.stem, "sequence prefix")
            if prefixes is not None and prefix not in prefixes:
                continue
            jobs.append({
                "output_root": str(root), "fbx_root": str(fbx_base),
                "trajectory": trajectory, "prefix": prefix, "fbx_path": str(fbx),
                "GT_dir": str(_under(root, trajectory, prefix + "_GT")),
                "occ_dir": str(_under(root, trajectory, prefix + "_occ")),
            })
    if limit is not None:
        jobs = jobs[:limit]
    if not jobs:
        raise ValueError("No FBX jobs match the requested trajectories/sequences")
    for job in jobs:
        validate_render_pair(job)
    return jobs


def load_jobs_manifest(path, output_root):
    """Load exact sequence pairs, avoiding a trajectory/prefix cross product."""
    root = Path(output_root).expanduser().resolve()
    jobs = json.loads(Path(path).read_text(encoding='utf-8'))
    if not isinstance(jobs, list) or not jobs:
        raise ValueError('Camera jobs manifest must be a nonempty list')
    seen = set()
    for job in jobs:
        if Path(job['output_root']).resolve() != root:
            raise ValueError('Camera jobs manifest has a different output root')
        identity = (job['trajectory'], job['prefix'])
        if identity in seen:
            raise ValueError('Duplicate sequence in camera jobs manifest')
        seen.add(identity)
        validate_render_pair(job)
    return jobs


def rigid_opencv_c2w(matrix_world) -> np.ndarray:
    """Remove only a uniform FBX object scale; retain meter-valued translation."""
    matrix = np.asarray(matrix_world, dtype=np.float64)
    if matrix.shape != (4, 4) or not np.isfinite(matrix).all():
        raise ValueError("Camera world matrix must be a finite 4x4 matrix")
    if not np.allclose(matrix[3], [0, 0, 0, 1], rtol=0, atol=1e-7):
        raise ValueError("Camera world matrix must be affine")
    linear = matrix[:3, :3]
    if np.linalg.det(linear) <= 0:
        raise ValueError("Camera matrix has a reflection or a singular scale")
    left, singular_values, right = np.linalg.svd(linear)
    scale = float(singular_values.mean())
    if scale <= 1e-12 or not np.allclose(singular_values, scale, rtol=1e-4, atol=1e-12):
        raise ValueError("Camera matrix contains nonuniform scale or shear")
    rotation = left @ right
    result = np.eye(4, dtype=np.float64)
    result[:3, :3] = rotation @ np.diag([1.0, -1.0, -1.0])
    result[:3, 3] = matrix[:3, 3]
    return result


def intrinsics_from_projection(projection, width=512, height=512) -> dict:
    """Convert Blender's render projection, including lens shifts, to pixel K."""
    if width != 512 or height != 512:
        raise ValueError("The camera protocol requires native 512x512 images")
    matrix = np.asarray(projection, dtype=np.float64)
    if matrix.shape != (4, 4) or not np.isfinite(matrix).all():
        raise ValueError("Projection must be a finite 4x4 matrix")
    if not np.allclose(matrix[3], [0, 0, -1, 0], rtol=0, atol=1e-7):
        raise ValueError("Only a perspective Blender projection is supported")
    if not np.allclose([matrix[0, 1], matrix[1, 0], matrix[0, 3], matrix[1, 3]], 0, rtol=0, atol=1e-7):
        raise ValueError("Projection contains unsupported skew or off-axis terms")
    fx, fy = float(matrix[0, 0] * width / 2), float(matrix[1, 1] * height / 2)
    cx, cy = float((1 - matrix[0, 2]) * width / 2), float((1 + matrix[1, 2]) * height / 2)
    if fx <= 0 or fy <= 0:
        raise ValueError("Projection focal lengths must be positive")
    if not (0 <= cx <= width and 0 <= cy <= height):
        raise ValueError("Principal point must lie within the native image for this protocol")
    return {"w": int(width), "h": int(height), "fl_x": fx, "fl_y": fy, "cx": cx, "cy": cy,
            "intrinsics": [[fx, 0.0, cx], [0.0, fy, cy], [0.0, 0.0, 1.0]]}


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _validate_payload(payload: Mapping) -> None:
    expected = {"schema_version": SCHEMA_VERSION, "coordinate_convention": "opencv_c2w",
                "translation_units": "m", "depth_units": "cm", "w": 512, "h": 512,
                "num_views": 25, "includes_occ_center": True, "view_order": "sequence",
                "pixel_center_offset": 0.5}
    for key, value in expected.items():
        if payload.get(key) != value:
            raise ValueError(f"Camera payload requires {key}={value!r}")
    for key in ("w", "h", "num_views", "center_idx"):
        if type(payload.get(key)) is not int:
            raise ValueError(f"Camera payload {key} must be an integer")
    trajectory = payload.get("trajectory")
    if trajectory not in TRAJECTORIES:
        raise ValueError("Camera payload has an unsupported trajectory")
    center = 24 if trajectory in ("rot_spiral", "rand_shell") else 12
    if payload.get("center_idx") != center:
        raise ValueError(f"Camera payload center index must be {center}")
    for key in ("fl_x", "fl_y", "cx", "cy"):
        if not np.isfinite(payload.get(key, np.nan)):
            raise ValueError(f"Camera payload {key} must be finite")
    if payload["fl_x"] <= 0 or payload["fl_y"] <= 0:
        raise ValueError("Camera payload focal lengths must be positive")
    if not (0 <= payload["cx"] <= 512 and 0 <= payload["cy"] <= 512):
        raise ValueError("Camera principal point must lie within the native image")
    frames = payload.get("frames", [])
    if len(frames) != 25:
        raise ValueError("Camera payload must have exactly 25 poses")
    for index, frame in enumerate(frames):
        if type(frame.get("frame_id")) is not int or frame["frame_id"] != index:
            raise ValueError("Camera frames must retain sequence indices 0..24")
        if frame.get("file_path") != f"../rgb/{index:04d}.png":
            raise ValueError("Camera frame has an unsafe or mismatched image path")
        pose = np.asarray(frame.get("transform_matrix"), dtype=np.float64)
        if pose.shape != (4, 4) or not np.isfinite(pose).all():
            raise ValueError("Camera poses must be finite 4x4 matrices")
        rotation = pose[:3, :3]
        if (not np.allclose(pose[3], [0, 0, 0, 1], atol=1e-7, rtol=0)
                or not np.allclose(rotation.T @ rotation, np.eye(3), atol=1e-5, rtol=0)
                or not np.isclose(np.linalg.det(rotation), 1.0, atol=1e-5, rtol=0)):
            raise ValueError("Camera poses must be proper rigid OpenCV c2w transforms")
    json.dumps(payload, allow_nan=False)


def build_payload(job: Mapping, poses: Sequence, intrinsics: Mapping, source_metadata: Mapping) -> dict:
    """Build JSON from already converted, rigid OpenCV c2w poses in render order."""
    validate_render_pair(job)
    _, _, _, fbx = _job_paths(job)
    if len(poses) != 25:
        raise ValueError("Exactly 25 poses are required; do not deduplicate stationary frames")
    trajectory = job["trajectory"]
    payload = dict(intrinsics)
    payload.update({
        "schema_version": SCHEMA_VERSION, "coordinate_convention": "opencv_c2w",
        "translation_units": "m", "depth_units": "cm", "pixel_center_offset": 0.5,
        "trajectory": trajectory, "sequence_prefix": job["prefix"],
        "num_views": 25, "includes_occ_center": True, "view_order": "sequence",
        "center_idx": 24 if trajectory in ("rot_spiral", "rand_shell") else 12,
        "k1": 0.0, "k2": 0.0, "k3": 0.0, "k4": 0.0, "p1": 0.0, "p2": 0.0,
        "frames": [{"frame_id": index, "file_path": f"../rgb/{index:04d}.png",
                    "transform_matrix": np.asarray(pose, dtype=np.float64).tolist()}
                   for index, pose in enumerate(poses)],
        "source": dict(source_metadata),
    })
    payload["source"].update({"fbx_path": str(fbx), "fbx_sha256": _sha256(fbx)})
    _validate_payload(payload)
    return payload


def _atomic_bytes(path: Path, content: bytes) -> None:
    descriptor, name = tempfile.mkstemp(prefix=".camera-json-", suffix=".tmp", dir=path.parent)
    temporary = Path(name)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(temporary, 0o644)
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def write_pair(job: Mapping, payload: Mapping, overwrite=False) -> dict:
    """Preflight both destinations; write byte-identical JSON by atomic replacement."""
    validate_render_pair(job)
    _validate_payload(payload)
    root, gt, occ, fbx = _job_paths(job)
    if payload["trajectory"] != job["trajectory"] or payload.get("sequence_prefix") != job["prefix"]:
        raise ValueError("Payload sequence does not match its destination job")
    source_hash = _sha256(fbx)
    if payload.get("source", {}).get("fbx_sha256") != source_hash:
        raise ValueError("Source FBX changed after camera extraction")
    content = (json.dumps(payload, indent=2, ensure_ascii=False, allow_nan=False) + "\n").encode("utf-8")
    paths = [_under(root, *directory.relative_to(root).parts, "pose", "transforms.json")
             for directory in (gt, occ)]
    previous = []
    same = []
    for path in paths:
        old = path.read_bytes() if path.is_file() else None
        previous.append(old)
        try:
            unchanged = old is not None and json.loads(old) == payload
        except (ValueError, UnicodeError):
            unchanged = False
        same.append(unchanged)
        if old is not None and not unchanged and not overwrite:
            raise FileExistsError(f"Refusing to overwrite different camera JSON: {path}")
        if path.exists() and not path.is_file():
            raise ValueError(f"Camera JSON destination is not a file: {path}")
    if not all(same):
        written = []
        try:
            for index, path in enumerate(paths):
                if same[index]:
                    continue
                path.parent.mkdir(parents=True, exist_ok=True)
                _atomic_bytes(path, content)
                written.append(index)
        except Exception:
            # Restore prior bytes in memory if the second atomic replacement fails.
            for index in reversed(written):
                if previous[index] is None:
                    paths[index].unlink(missing_ok=True)
                else:
                    _atomic_bytes(paths[index], previous[index])
            raise
    return {"status": "unchanged" if all(same) else "written", "paths": [str(path) for path in paths],
            "sha256": hashlib.sha256(content).hexdigest(), "source_fbx_sha256": source_hash}
