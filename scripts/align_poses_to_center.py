#!/usr/bin/env python3
"""
Refocus images to a specific depth plane using proper geometric transformation
"""

import json
import numpy as np
import sys
import os
import cv2
import re
from pathlib import Path
import OpenEXR
import Imath

def extract_depth_from_folder_name(folder_name):
    """
    Extract depth value from folder name like 'scene_001_Target_001_height_030_GT'
    Returns depth in meters
    """
    match = re.search(r'height_(\d+)', folder_name)
    if match:
        # Convert from centimeters to meters (030 -> 0.30m)
        return int(match.group(1)) / 100.0
    return None

def refocus_image(src_img, src_pose, center_pose, K, depth):
    """
    Refocus source image to specified depth plane using proper geometric transformation
    """
    h, w = src_img.shape[:2]
    
    # Camera intrinsics
    K_inv = np.linalg.inv(K)
    
    # Rotation and translation from center to source
    R_center = center_pose[:3, :3]
    T_center = center_pose[:3, 3:4]
    R_src = src_pose[:3, :3]
    T_src = src_pose[:3, 3:4]
    
    # Transform from center to source camera
    R_c2s = R_src @ R_center.T
    T_c2s = T_src - R_c2s @ T_center
    
    # Create pixel coordinates for center camera
    u, v = np.meshgrid(np.arange(w), np.arange(h))
    ones = np.ones_like(u)
    pixels = np.stack([u.flatten(), v.flatten(), ones.flatten()], axis=0)
    
    # Convert to rays in center camera coordinate
    rays = K_inv @ pixels
    
    # Transform rays to source camera and project to depth plane
    transformed_rays = R_c2s @ rays + T_c2s / depth
    
    # Project back to source image coordinates
    projected = K @ transformed_rays
    
    # Extract x, y coordinates
    x_coords = projected[0, :].reshape(h, w)
    y_coords = projected[1, :].reshape(h, w)
    
    # Apply remap
    refocused_img = cv2.remap(src_img, x_coords.astype(np.float32), y_coords.astype(np.float32), cv2.INTER_LINEAR)
    
    return refocused_img
def process_dataset(transforms_file, rgb_dir, output_dir, sequence_name):
    """
    Process dataset: refocus images to the depth plane specified in folder name
    """
    # Extract depth from sequence name
    depth = extract_depth_from_folder_name(sequence_name)
    if depth is None:
        print(f"Could not extract depth from folder name: {sequence_name}")
        return
    
    print(f"Refocusing to depth plane: {depth:.2f}m")
    
    # Load transforms
    with open(transforms_file, 'r') as f:
        pose_data = json.load(f)
    
    # Get camera intrinsics
    K = np.array([
        [pose_data['fl_x'], 0, pose_data['cx']],
        [0, pose_data['fl_y'], pose_data['cy']],
        [0, 0, 1]
    ])
    
    # Find center frame
    frames = pose_data['frames']
    center_idx = len(frames) // 2
    center_pose = np.array(frames[center_idx]['transform_matrix'])
    
    # Create output directory
    os.makedirs(os.path.join(output_dir, 'rgb'), exist_ok=True)
    
    # Process each frame
    for i, frame in enumerate(frames):
        # Load RGB image
        rgb_path = os.path.join(rgb_dir, f"{i:04d}.png")
        
        if not os.path.exists(rgb_path):
            continue
            
        rgb_img = cv2.imread(rgb_path)
        
        # Get pose
        src_pose = np.array(frame['transform_matrix'])
        
        # Refocus to specified depth plane
        refocused_rgb = refocus_image(rgb_img, src_pose, center_pose, K, depth)
        
        # Save refocused image
        cv2.imwrite(os.path.join(output_dir, 'rgb', f"{i:04d}.png"), refocused_rgb)
    
    # Save original transforms (no modification needed for refocused images)
    os.makedirs(os.path.join(output_dir, 'pose'), exist_ok=True)
    with open(os.path.join(output_dir, 'pose', 'transforms.json'), 'w') as f:
        json.dump(pose_data, f, indent=2)
    
    print(f"Processed {len(frames)} frames to {output_dir}")

def batch_process_render_data():
    """
    Batch process all render data with refocusing
    """
    base_dir = "/home_ssd/sjy/UE5_Project/PCGBiomeForestPoplar/Saved/MovieRenders/render_data"
    output_base = "./refocus_data"
    
    # Find all sequence directories
    for trajectory_type in os.listdir(base_dir):
        trajectory_dir = os.path.join(base_dir, trajectory_type)
        if not os.path.isdir(trajectory_dir):
            continue
            
        for sequence_name in os.listdir(trajectory_dir):
            sequence_dir = os.path.join(trajectory_dir, sequence_name)
            if not os.path.isdir(sequence_dir):
                continue
                
            # Check for required files
            transforms_file = os.path.join(sequence_dir, "pose", "transforms.json")
            rgb_dir = os.path.join(sequence_dir, "rgb")
            
            if not all(os.path.exists(p) for p in [transforms_file, rgb_dir]):
                print(f"Skipping {sequence_name}: missing required files")
                continue
            
            # Create output directory
            output_dir = os.path.join(output_base, trajectory_type, sequence_name)
            
            print(f"Processing {trajectory_type}/{sequence_name}")
            process_dataset(transforms_file, rgb_dir, output_dir, sequence_name)

def main():
    if len(sys.argv) == 1:
        # Batch mode
        batch_process_render_data()
    elif len(sys.argv) == 4:
        # Manual mode
        transforms_file = sys.argv[1]
        rgb_dir = sys.argv[2]
        output_dir = sys.argv[3]
        sequence_name = os.path.basename(output_dir)
        process_dataset(transforms_file, rgb_dir, output_dir, sequence_name)
    else:
        print("Usage:")
        print("  Batch mode: python align_poses_to_center.py")
        print("  Manual mode: python align_poses_to_center.py <transforms.json> <rgb_dir> <output_dir>")
        sys.exit(1)

if __name__ == "__main__":
    main()