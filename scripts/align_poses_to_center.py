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
        # Convert from centimeters to meters  注意：转换到blender中相机朝向的是-Z轴，因此这样设计
        return -int(match.group(1)) 
    return None

import numpy as np
import cv2

def refocus_image(src_img, src_pose, center_pose, K, depth):
    """
    Refocus source image to specified depth plane.
    Inputs:
        src_img: Source image (H, W, 3)
        src_pose: Source Camera-to-World matrix (4x4)
        center_pose: Center Camera-to-World matrix (4x4)
        K: Intrinsic matrix (3x3)
        depth: Scalar depth value of the plane
    """
    h, w = src_img.shape[:2]
    
    # Camera intrinsics
    K_inv = np.linalg.inv(K)
    
    # 1. Extract rotation and translation matrices (C2W)
    R_center = center_pose[:3, :3]
    T_center = center_pose[:3, 3:4]
    
    R_src = src_pose[:3, :3]
    T_src = src_pose[:3, 3:4]  # 【修正1】原代码这里把 R_src 覆盖了，必须修正！
    
    # 2. Calculate Relative Transform: Center -> Source
    # 因为输入是 C2W，我们需要计算 M_src^-1 * M_center
    # 公式推导：
    # P_src = R_src^T * (P_world - T_src)
    # P_world = R_center * P_center + T_center
    # 代入得: P_src = R_src^T * (R_center * P_center + T_center - T_src)
    #               = (R_src^T @ R_center) * P_center + R_src^T @ (T_center - T_src)
    
    R_c2s = R_src.T @ R_center                        # 相对旋转
    T_c2s = R_src.T @ (T_center - T_src)              # 相对平移
    
    # 3. Create pixel coordinates for center camera
    u, v = np.meshgrid(np.arange(w), np.arange(h))
    ones = np.ones_like(u)
    
    # Stack to homogeneous coordinates [3, H*W]
    pixels = np.stack([u.flatten(), v.flatten(), ones.flatten()], axis=0)
    
    # 4. Unproject to rays in center camera coordinate system
    # rays 也就是归一化坐标 (x/z, y/z, 1)
    rays_center = K_inv @ pixels  # [3, H*W]
    
    # 5. Apply depth and transform to source camera
    # 原理: P_center = rays_center * depth
    #       P_src    = R_c2s @ P_center + T_c2s
    #                = R_c2s @ (rays_center * depth) + T_c2s
    #       投影时我们只需要比例关系，所以两边同时除以 depth (为了数值稳定性，防止大数)
    #       P_src_normalized = R_c2s @ rays_center + T_c2s / depth
    
    transformed_points = R_c2s @ rays_center + T_c2s / depth
    
    # 6. Project back to source image coordinates
    projected = K @ transformed_points
    
    # 7. Normalize by Z coordinate (Perspective Division) and reshape
    # 加上 1e-6 防止除以 0
    z = projected[2, :] + 1e-6
    x_coords = (projected[0, :] / z).reshape(h, w)
    y_coords = (projected[1, :] / z).reshape(h, w)
    
    # Debug info (Optional)
    # print(f"Reprojection bounds: X[{x_coords.min():.1f}, {x_coords.max():.1f}], Y[{y_coords.min():.1f}, {y_coords.max():.1f}]")
    
    # 8. Apply remap with boundary handling
    refocused_img = cv2.remap(src_img, 
                              x_coords.astype(np.float32), 
                              y_coords.astype(np.float32), 
                              interpolation=cv2.INTER_LINEAR, 
                              borderMode=cv2.BORDER_CONSTANT, 
                              borderValue=0)
    
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