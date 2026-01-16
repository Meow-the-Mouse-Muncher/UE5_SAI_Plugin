#!/usr/bin/env python3
"""
Improved Blender script based on your provided code
Extracts camera transforms from imported FBX files and exports to JSON format
Compatible with 3D Gaussian Splatting training data format
Integrates generate_transforms_3dgs.py processing logic
Reads configuration from MatrixCityPlugin config files
"""

import sys
# Add user's local Python packages to path for snap Blender
sys.path.append('/home/user1/.local/lib/python3.11/site-packages')

import bpy
import os
import json
import glob
import numpy as np
import yaml

def load_config():
    """
    Load configuration from MatrixCityPlugin config files
    """
    project_dir = "/home_ssd/sjy/UE5_Project/PCGBiomeForestPoplar"
    user_config_path = os.path.join(project_dir, "Plugins/MatrixCityPlugin/misc/user.json")
    render_config_path = os.path.join(project_dir, "Plugins/MatrixCityPlugin/misc/render_SAI_config.yaml")
    
    config = {}
    
    # Load user.json
    try:
        with open(user_config_path, 'r') as f:
            user_config = json.load(f)
            config.update(user_config)
        print(f"Loaded user config from: {user_config_path}")
    except Exception as e:
        print(f"Warning: Could not load user config: {e}")
    
    # Load render config
    try:
        with open(render_config_path, 'r') as f:
            render_config = yaml.safe_load(f)
            config.update(render_config)
        print(f"Loaded render config from: {render_config_path}")
    except Exception as e:
        print(f"Warning: Could not load render config: {e}")
    
    return config

def listify_matrix(matrix):
    """Convert Blender matrix to list format"""
    matrix_list = []
    for row in matrix:
        matrix_list.append(list(row))
    return matrix_list

def get_camera_keyframes(camera_obj):
    """
    Detect frames where camera transform actually changes
    """
    scene = bpy.context.scene
    keyframes = []
    
    # Store original frame
    original_frame = scene.frame_current
    
    # Sample every frame and detect changes
    previous_matrix = None
    tolerance = 1e-6
    
    for frame in range(scene.frame_start, scene.frame_end + 1):
        scene.frame_set(frame)
        bpy.context.view_layer.update()
        
        current_matrix = camera_obj.matrix_world.copy()
        
        # Check if transform changed from previous frame
        if previous_matrix is not None:
            diff_found = False
            for i in range(4):
                for j in range(4):
                    if abs(current_matrix[i][j] - previous_matrix[i][j]) > tolerance:
                        diff_found = True
                        break
                if diff_found:
                    break
            
            if diff_found:
                keyframes.append(frame)
        else:
            # Always include first frame
            keyframes.append(frame)
        
        previous_matrix = current_matrix
    
    # Restore original frame
    scene.frame_set(original_frame)
    
    return keyframes

def clear_scene():
    """Clear all objects from the scene"""
    bpy.ops.object.select_all(action='SELECT')
    bpy.ops.object.delete(use_global=False)

def generate_3dgs_transforms_direct(out_data, output_dir, config, scale=100):
    """
    Convert raw transforms to 3DGS format directly from memory
    Generate JSON files for both GT and occ folders
    """
    all_frames = []
    frames = out_data.get('frames', [])
    
    # Get resolution from config, fallback to default
    resolution = config.get('Resolution', [1920, 1080])
    w = float(resolution[0])
    h = float(resolution[1])
    
    print(f"Using resolution from config: {int(w)}x{int(h)}")

    for frame in frames:
        # Generate image file path (assuming images will be in rgb subdirectory)
        image_name = str(frame['frame_index']).zfill(4) + '.png'
        file_path = os.path.join("..", "rgb", image_name)

        c2w = np.array(frame['rot_mat'])
        
        # Transformation logic from generate_transforms_3dgs.py
        c2w[:3, :3] *= 100
        # c2w[:3, 3] /= scale
        
        all_frames.append({
            'file_path': file_path,
            'transform_matrix': c2w.tolist()
        })

    # Camera intrinsics
    angle_x = out_data['camera_angle_x']
    fl_x = float(.5 * w / np.tan(.5 * angle_x))
    fl_y = fl_x
    
    # Distortion parameters (default to 0)
    k1 = k2 = k3 = k4 = p1 = p2 = 0
    cx = w / 2
    cy = h / 2
    
    pose = {
        "camera_angle_x": angle_x,
        "fl_x": fl_x,
        "fl_y": fl_y,
        "k1": k1, "k2": k2, "k3": k3, "k4": k4,
        "p1": p1, "p2": p2,
        "cx": cx, "cy": cy,
        "w": w, "h": h,
        "frames": all_frames
    }
    
    fbx_name = os.path.basename(output_dir)
    # 获取父目录（轨迹类型目录）
    parent_dir = os.path.dirname(output_dir)
    output_files = []
    
    # 为 GT 和 occ 都创建目录和 JSON 文件
    for suffix in ["_GT", "_occ"]:
        target_name = fbx_name + suffix
        target_dir = os.path.join(parent_dir, target_name)
        pose_dir = os.path.join(target_dir, "pose")
        os.makedirs(pose_dir,exist_ok=True)
        
        output_file = os.path.join(pose_dir, "transforms.json")
        with open(output_file, "w") as outfile:
            json.dump(pose, outfile, indent=2)
        
        output_files.append(output_file)
        print(f"Generated 3DGS transforms ({suffix[1:]}): {output_file} with {len(all_frames)} frames")
    
    return output_files
    
    # 原有逻辑：只生成一个 JSON 文件
    pose_dir = os.path.join(output_dir, "pose")
    if not os.path.exists(pose_dir):
        os.makedirs(pose_dir)
    
    output_file = os.path.join(pose_dir, "transforms.json")
    with open(output_file, "w") as outfile:
        json.dump(pose, outfile, indent=2)
        
    print(f"Generated 3DGS transforms: {output_file} with {len(all_frames)} frames")
    return output_file

def generate_3dgs_transforms(raw_transforms_path, output_dir, config, scale=100):
    """
    Convert raw transforms to 3DGS format (from generate_transforms_3dgs.py logic)
    """
    with open(raw_transforms_path, "r") as f:
        tj = json.load(f)

    all_frames = []
    frames = tj.get('frames', [])
    
    # Get resolution from config, fallback to default
    resolution = config.get('Resolution', [1920, 1080])
    w = float(resolution[0])
    h = float(resolution[1])
    
    print(f"Using resolution from config: {int(w)}x{int(h)}")

    for frame in frames:
        # Generate image file path (assuming images will be in rgb subdirectory)
        image_name = str(frame['frame_index']).zfill(4) + '.png'
        file_path = os.path.join("..", "rgb", image_name)

        c2w = np.array(frame['rot_mat'])
        
        # Transformation logic from generate_transforms_3dgs.py
        c2w[:3, :3] *= 100
        c2w[:3, 3] /= scale
        
        all_frames.append({
            'file_path': file_path,
            'transform_matrix': c2w.tolist()
        })

    # Camera intrinsics
    angle_x = tj['camera_angle_x']
    fl_x = float(.5 * w / np.tan(.5 * angle_x))
    fl_y = fl_x
    
    # Distortion parameters (default to 0)
    k1 = k2 = k3 = k4 = p1 = p2 = 0
    cx = w / 2
    cy = h / 2
    
    pose = {
        "camera_angle_x": angle_x,
        "fl_x": fl_x,
        "fl_y": fl_y,
        "k1": k1, "k2": k2, "k3": k3, "k4": k4,
        "p1": p1, "p2": p2,
        "cx": cx, "cy": cy,
        "w": w, "h": h,
        "frames": all_frames
    }
    
    # Create pose directory
    pose_dir = os.path.join(output_dir, "pose")
    if not os.path.exists(pose_dir):
        os.makedirs(pose_dir)
    
    output_file = os.path.join(pose_dir, "transforms.json")
    with open(output_file, "w") as outfile:
        json.dump(pose, outfile, indent=2)
        
    print(f"Generated 3DGS transforms: {output_file} with {len(all_frames)} frames")
    return output_file

def process_single_fbx(fbx_path, output_base_dir, config, scale=100):
    """
    Process a single FBX file and extract camera transforms
    """
    # Clear scene
    clear_scene()
    
    # Import FBX
    try:
        bpy.ops.import_scene.fbx(filepath=fbx_path)
        print(f"Successfully imported: {os.path.basename(fbx_path)}")
    except Exception as e:
        print(f"Failed to import {fbx_path}: {e}")
        return False
    
    # Find camera object
    cam = None
    camera_name = config.get('camera_name', 'CineCameraActor1')
    
    # First try to find camera by name from config
    for obj in bpy.context.scene.objects:
        if obj.type == 'CAMERA' and camera_name in obj.name:
            cam = obj
            break
    
    # If not found, get any camera
    if not cam:
        for obj in bpy.context.scene.objects:
            if obj.type == 'CAMERA':
                cam = obj
                break
    
    if not cam:
        print(f"No camera found in {os.path.basename(fbx_path)}")
        return False
    
    print(f"Using camera: {cam.name}")
    
    # Select the camera (following your code pattern)
    bpy.context.view_layer.objects.active = cam
    cam.select_set(True)
    
    scene = bpy.context.scene
    
    # Prepare output data structure (matching your format exactly)
    out_data = {
        'camera_angle_x': cam.data.angle_x,
        'frames': []
    }
    
    # Create output directory based on FBX filename
    fbx_name = os.path.splitext(os.path.basename(fbx_path))[0]
    output_dir = os.path.join(output_base_dir, fbx_name)  
    
    # Get keyframes by detecting camera pose changes
    keyframes = get_camera_keyframes(cam)
    
    if not keyframes:
        print(f"No pose changes detected, using single frame")
        keyframes = [scene.frame_start]
    
    print(f"Processing {len(keyframes)} frames with pose changes")
    
    # Extract transforms for each keyframe
    for i, frame in enumerate(keyframes):
        scene.frame_set(frame)
        bpy.context.view_layer.update()
        
        frame_data = {
            'frame_index': i,
            'rot_mat': listify_matrix(cam.matrix_world)
        }
        out_data['frames'].append(frame_data)
    
    try:
        # Generate 3DGS format transforms directly
        final_transforms = generate_3dgs_transforms_direct(out_data, output_dir, config, scale)
        
        print(f"Successfully processed: {fbx_name}")
        print(f"  - Frames: {len(out_data['frames'])}")
        print(f"  - Camera FOV: {cam.data.angle_x:.4f} radians")
        print(f"  - Final transforms: {final_transforms}")
        
        return True
    except Exception as e:
        print(f"Failed to process {fbx_name}: {e}")
        return False

def batch_process_by_category(scale=100):
    """
    Batch process FBX files organized by category (fix_line, rot_arc, rot_line)
    """
    # Load configuration
    config = load_config()
    
    # Base paths
    project_dir = "/home_ssd/sjy/UE5_Project/PCGBiomeForestPoplar"
    fbx_dir = os.path.join(project_dir, "Content","Exported_FBX_test")
    
    # Use output path from config if available
    if 'Output_Path' in config:
        output_base_dir = config['Output_Path']
    else:
        output_base_dir = os.path.join(project_dir, "3dgs_data")
    
    if not os.path.exists(fbx_dir):
        print(f"FBX directory not found: {fbx_dir}")
        return
    
    # Create base output directory
    if not os.path.exists(output_base_dir):
        os.makedirs(output_base_dir)
    
    # Categories based on your sequence structure
    categories = ["fix_line", "rot_arc", "rot_line"]
    
    total_processed = 0
    total_files = 0
    
    print(f"Configuration loaded:")
    print(f"  - Resolution: {config.get('Resolution', [1920, 1080])}")
    print(f"  - Camera name: {config.get('camera_name', 'CineCameraActor1')}")
    print(f"  - Map: {config.get('ue_map', 'N/A')}")
    print(f"  - Scale factor: {scale}")
    
    for category in categories:
        print(f"\n{'='*50}")
        print(f"Processing category: {category}")
        print(f"{'='*50}")
        
        # Find FBX files for this category
        category_fbx_dir = os.path.join(fbx_dir, category)
        if not os.path.exists(category_fbx_dir):
            print(f"Category directory not found: {category_fbx_dir}")
            continue
            
        pattern = os.path.join(category_fbx_dir, "*.fbx")
        fbx_files = glob.glob(pattern)
        
        if not fbx_files:
            print(f"No FBX files found for category: {category}")
            continue
        
        print(f"Found {len(fbx_files)} files for {category}")
        total_files += len(fbx_files)
        
        # Create category output directory
        category_output_dir = os.path.join(output_base_dir, category)
        
        # Process each FBX file
        for fbx_path in fbx_files:
            print(f"\nProcessing: {os.path.basename(fbx_path)}")
            if process_single_fbx(fbx_path, category_output_dir, config, scale):
                total_processed += 1
    
    print(f"\n{'='*60}")
    print(f"Batch processing completed!")
    print(f"Successfully processed: {total_processed}/{total_files} files")
    print(f"Output directory: {output_base_dir}")
    print(f"Scale factor used: {scale}")
    print(f"{'='*60}")

def main():
    """Main function"""
    print("Blender Camera Transform Extractor + 3DGS Converter")
    print("Based on 3D Gaussian Splatting format")
    print("Reading configuration from MatrixCityPlugin")
    
    # You can modify the scale factor here if needed
    scale_factor = 100
    batch_process_by_category(scale_factor)



if __name__ == "__main__":
    main()