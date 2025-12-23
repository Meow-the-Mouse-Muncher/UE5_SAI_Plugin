#!/usr/bin/env python3
"""
Improved Blender script based on your provided code
Extracts camera transforms from imported FBX files and exports to JSON format
Compatible with 3D Gaussian Splatting training data format
"""

import bpy
import os
import json
import glob

def listify_matrix(matrix):
    """Convert Blender matrix to list format"""
    matrix_list = []
    for row in matrix:
        matrix_list.append(list(row))
    return matrix_list

def clear_scene():
    """Clear all objects from the scene"""
    bpy.ops.object.select_all(action='SELECT')
    bpy.ops.object.delete(use_global=False)

def process_single_fbx(fbx_path, output_base_dir):
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
    camera = None
    for obj in bpy.context.scene.objects:
        if obj.type == 'CAMERA':
            camera = obj
            break
    
    if not camera:
        print(f"No camera found in {os.path.basename(fbx_path)}")
        return False
    
    # Select the camera
    bpy.context.view_layer.objects.active = camera
    camera.select_set(True)
    
    scene = bpy.context.scene
    
    # Prepare output data structure (matching your format)
    out_data = {
        'camera_angle_x': camera.data.angle_x,
        'frames': []
    }
    
    # Create output directory based on FBX filename
    fbx_name = os.path.splitext(os.path.basename(fbx_path))[0]
    output_dir = os.path.join(output_base_dir, fbx_name)
    
    if not os.path.exists(output_dir):
        os.makedirs(output_dir)
    
    # Extract transforms for each frame
    for i, frame in enumerate(range(scene.frame_start, scene.frame_end + 1)):
        scene.frame_set(frame)
        
        # Update scene to get current frame transforms
        bpy.context.view_layer.update()
        
        frame_data = {
            'frame_index': i,
            'rot_mat': listify_matrix(camera.matrix_world)
        }
        out_data['frames'].append(frame_data)
    
    # Write transforms.json file (matching your naming convention)
    json_path = os.path.join(output_dir, 'transforms.json')
    
    try:
        with open(json_path, 'w') as out_file:
            json.dump(out_data, out_file, indent=4)
        print(f"Successfully exported: {json_path}")
        print(f"  - Frames: {len(out_data['frames'])}")
        print(f"  - Camera FOV: {camera.data.angle_x:.4f} radians")
        return True
    except Exception as e:
        print(f"Failed to write JSON {json_path}: {e}")
        return False

def batch_process_by_category():
    """
    Batch process FBX files organized by category (fix_line, rot_arc, rot_line)
    """
    # Base paths
    project_dir = "/home_ssd/sjy/UE5_Project/PCGBiomeForestPoplar"
    fbx_dir = os.path.join(project_dir, "Exported_FBX")
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
    
    for category in categories:
        print(f"\n{'='*50}")
        print(f"Processing category: {category}")
        print(f"{'='*50}")
        
        # Find FBX files for this category
        pattern = os.path.join(fbx_dir, f"*{category}*.fbx")
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
            if process_single_fbx(fbx_path, category_output_dir):
                total_processed += 1
    
    print(f"\n{'='*60}")
    print(f"Batch processing completed!")
    print(f"Successfully processed: {total_processed}/{total_files} files")
    print(f"Output directory: {output_base_dir}")
    print(f"{'='*60}")

def main():
    """Main function"""
    print("Blender Camera Transform Extractor")
    print("Based on 3D Gaussian Splatting format")
    
    batch_process_by_category()

if __name__ == "__main__":
    main()