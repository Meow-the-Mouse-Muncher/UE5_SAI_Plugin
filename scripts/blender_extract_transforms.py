#!/usr/bin/env python3
"""Headless FBX camera export for the 25-view, native-512 data protocol.

Normally launched by export_camera_json.py. This script only reads existing
FBX files and writes camera metadata; it never renders or inspects images.
"""
import argparse
import hashlib
import json
from pathlib import Path
import sys

if __name__ == '__main__':
    sys.dont_write_bytecode = True

SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))
from camera_json_protocol import (build_payload, discover_jobs, load_jobs_manifest, intrinsics_from_projection,
                                  rigid_opencv_c2w, write_pair)


def arguments(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output-root', required=True)
    parser.add_argument('--fbx-root')
    parser.add_argument('--trajectory', action='append')
    parser.add_argument('--sequence', action='append')
    parser.add_argument('--limit', type=int)
    parser.add_argument('--camera-name', default='CineCameraActor1')
    parser.add_argument('--overwrite', action='store_true')
    parser.add_argument('--report', required=True)
    parser.add_argument('--jobs-file')
    parser.add_argument('--dry-run', action='store_true')
    return parser.parse_args(argv)


def _clear_scene(bpy):
    bpy.ops.object.select_all(action='SELECT')
    bpy.ops.object.delete(use_global=False)
    for action in list(bpy.data.actions):
        bpy.data.actions.remove(action)
    scene = bpy.context.scene
    scene.unit_settings.system = 'METRIC'
    scene.unit_settings.scale_length = 1.0
    scene.render.resolution_x = scene.render.resolution_y = 512
    scene.render.resolution_percentage = 100
    scene.render.pixel_aspect_x = scene.render.pixel_aspect_y = 1.0
    return scene


def _camera(bpy, requested):
    cameras = [obj for obj in bpy.context.scene.objects if obj.type == 'CAMERA']
    exact = [obj for obj in cameras if obj.name == requested]
    matching = exact or [obj for obj in cameras if requested in obj.name]
    if len(matching) == 1:
        return matching[0]
    if not matching and len(cameras) == 1:
        return cameras[0]
    raise ValueError(f'Camera selection is ambiguous: {[obj.name for obj in cameras]}; set --camera-name')


def _animation_range(camera):
    intervals = []
    objects = []
    current = camera
    while current is not None:
        objects.append(current)
        current = current.parent
    objects.append(camera.data)
    for obj in objects:
        animation = getattr(obj, 'animation_data', None)
        action = getattr(animation, 'action', None) if animation else None
        if action is not None:
            intervals.append(tuple(float(value) for value in action.frame_range))
    if not intervals:
        raise ValueError('FBX camera has no animation; cannot prove its 25-frame alignment')
    low = min(item[0] for item in intervals)
    high = max(item[1] for item in intervals)
    if abs(low) > 1e-3 or high < 24-1e-3 or high > 25+1e-3:
        raise ValueError(f'FBX camera animation range {low, high} is incompatible with frames 0..24')
    return low, high


def extract_job(job, camera_name):
    import bpy
    import numpy as np

    source_hash = hashlib.sha256(Path(job['fbx_path']).read_bytes()).hexdigest()
    scene = _clear_scene(bpy)
    # Honor the FBX's declared units. UE's centimeter scene is converted into
    # this metric, scale_length=1 scene by Blender, with no fixed *100 repair.
    try:
        bpy.ops.import_scene.fbx.get_rna_type()
    except (AttributeError, RuntimeError):
        import addon_utils
        addon_utils.enable('io_scene_fbx', default_set=False, persistent=False)
    result = bpy.ops.import_scene.fbx(filepath=job['fbx_path'], global_scale=1.0,
        use_anim=True, anim_offset=0.0, use_manual_orientation=False,
        bake_space_transform=False)
    if 'FINISHED' not in result:
        raise RuntimeError(f'FBX import failed: {job["fbx_path"]}')
    camera = _camera(bpy, camera_name)
    if camera.data.type != 'PERSP':
        raise ValueError('Only perspective pinhole cameras are supported')
    low, high = _animation_range(camera)
    poses, intrinsic = [], None
    raw_scales = []
    for index in range(25):
        scene.frame_set(index)
        bpy.context.view_layer.update()
        depsgraph = bpy.context.evaluated_depsgraph_get()
        evaluated = camera.evaluated_get(depsgraph)
        matrix = np.asarray(evaluated.matrix_world, dtype=np.float64)
        raw_scales.append(np.linalg.norm(matrix[:3, :3], axis=0).tolist())
        poses.append(rigid_opencv_c2w(matrix))
        projection = evaluated.calc_matrix_camera(depsgraph, x=512, y=512,
                                                  scale_x=1.0, scale_y=1.0)
        current = intrinsics_from_projection(np.asarray(projection, dtype=np.float64))
        if intrinsic is None:
            intrinsic = current
        elif not np.allclose([current[key] for key in ('fl_x','fl_y','cx','cy')],
                             [intrinsic[key] for key in ('fl_x','fl_y','cx','cy')],
                             atol=1e-5, rtol=1e-6):
            raise ValueError('Animated camera intrinsics require a different protocol; fixed K cannot represent them')
    endpoint_hold = False
    if high > 24+1e-3:
        scene.frame_set(25)
        bpy.context.view_layer.update()
        end_pose = rigid_opencv_c2w(np.asarray(camera.evaluated_get(
            bpy.context.evaluated_depsgraph_get()).matrix_world, dtype=np.float64))
        if not np.allclose(end_pose, poses[-1], atol=1e-5, rtol=0):
            raise ValueError('The extra FBX endpoint is not a duplicate hold; refuse to guess frame mapping')
        endpoint_hold = True
    metadata = {
        'fbx_sha256_before_import': source_hash,
        'blender_version': bpy.app.version_string,
        'blender_build_hash': bpy.app.build_hash.decode('ascii', errors='replace'),
        'camera_name': camera.name,
        'blender_scene_units': 'METRIC', 'blender_scale_length': 1.0,
        'unit_conversion': 'FBX-declared units to Blender meters; scale removed from rotation only',
        'frame_sampling': 'consecutive source frames, never pose-change deduplication',
        'anim_offset': 0.0, 'source_frame_indices': list(range(25)),
        'source_animation_range': [low, high], 'ignored_duplicate_endpoint_hold': endpoint_hold,
        'fps': float(scene.render.fps / scene.render.fps_base),
        'imported_linear_column_scales': raw_scales,
        'world_basis': 'Blender FBX importer world basis, shared for all camera poses',
    }
    if hashlib.sha256(Path(job['fbx_path']).read_bytes()).hexdigest() != source_hash:
        raise ValueError('Source FBX changed while importing or sampling its camera')
    return build_payload(job, poses, intrinsic, metadata)


def main(argv=None):
    args = arguments(argv)
    jobs = (load_jobs_manifest(args.jobs_file, args.output_root) if args.jobs_file else
            discover_jobs(args.output_root, args.fbx_root, args.trajectory, args.sequence, args.limit))
    if args.dry_run:
        print(json.dumps({'dry_run': True, 'jobs': jobs}, indent=2))
        return
    completed = []
    for job in jobs:
        payload = extract_job(job, args.camera_name)
        result = write_pair(job, payload, overwrite=args.overwrite)
        result.update(trajectory=job['trajectory'], prefix=job['prefix'])
        completed.append(result)
        print(json.dumps(result), flush=True)
    report = {'schema_version': 'camera-export-report-v1', 'num_views': 25,
              'width': 512, 'height': 512, 'coordinate_convention': 'opencv_c2w',
              'translation_units': 'm', 'depth_units': 'cm', 'jobs': completed,
              'output_root': str(Path(args.output_root).resolve())}
    path = Path(args.report).resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name+'.tmp')
    temporary.write_text(json.dumps(report, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')
    temporary.replace(path)
    print(f'Camera JSON export complete: {len(completed)} sequences; report: {path}', flush=True)


if __name__ == '__main__':
    argv = sys.argv[sys.argv.index('--')+1:] if '--' in sys.argv else sys.argv[1:]
    main(argv)
