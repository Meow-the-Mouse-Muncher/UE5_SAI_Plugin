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
from tqdm import tqdm
from concurrent.futures import ThreadPoolExecutor, ProcessPoolExecutor
import multiprocessing as mp

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

def precompute_transforms(poses, center_pose, K, depth):
    """
    预计算所有帧的变换矩阵，避免重复计算
    """
    K_inv = np.linalg.inv(K)
    R_center = center_pose[:3, :3]
    T_center = center_pose[:3, 3:4]
    
    # 分离共享数据和每帧独有数据
    shared_data = (K, K_inv, depth)
    frame_transforms = []
    
    for pose in poses:
        R_src = pose[:3, :3]
        T_src = pose[:3, 3:4]
        
        # Calculate relative transform
        R_c2s = R_src.T @ R_center
        T_c2s = R_src.T @ (T_center - T_src)
        
        frame_transforms.append((R_c2s, T_c2s))
    
    return shared_data, frame_transforms

def refocus_image_fast(src_img, frame_transform, shared_data):
    """
    使用预计算的变换数据快速重聚焦图像
    """
    R_c2s, T_c2s = frame_transform
    K, K_inv, depth = shared_data
    h, w = src_img.shape[:2]
    
    # Create pixel coordinates
    u, v = np.meshgrid(np.arange(w), np.arange(h))
    ones = np.ones_like(u)
    pixels = np.stack([u.flatten(), v.flatten(), ones.flatten()], axis=0)
    
    # Unproject and transform
    rays_center = K_inv @ pixels
    transformed_points = R_c2s @ rays_center + T_c2s / depth
    projected = K @ transformed_points
    
    # Perspective division
    z = projected[2, :] + 1e-6
    x_coords = (projected[0, :] / z).reshape(h, w).astype(np.float32)
    y_coords = (projected[1, :] / z).reshape(h, w).astype(np.float32)
    
    # Remap
    return cv2.remap(src_img, x_coords, y_coords, 
                     interpolation=cv2.INTER_LINEAR, 
                     borderMode=cv2.BORDER_CONSTANT, 
                     borderValue=0)

def process_single_frame(args):
    """
    处理单帧的工作函数，用于多进程
    """
    frame_idx, rgb_path, output_path, frame_transform, shared_data = args
    
    if not os.path.exists(rgb_path):
        return False
    
    # Load image
    rgb_img = cv2.imread(rgb_path)
    if rgb_img is None:
        return False
    
    # Refocus
    refocused_rgb = refocus_image_fast(rgb_img, frame_transform, shared_data)
    
    # Save
    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    cv2.imwrite(output_path, refocused_rgb)
    
    return True

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
    T_src = src_pose[:3, 3:4]  
    
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
def process_dataset(transforms_file, rgb_dir, output_dir, sequence_name, num_workers=None):
    """
    Process dataset: refocus images to the depth plane specified in folder name
    使用多进程加速处理
    """
    if num_workers is None:
        num_workers = min(mp.cpu_count(), 16)  # 限制最大进程数
    
    # Extract depth from sequence name
    depth = extract_depth_from_folder_name(sequence_name)
    if depth is None:
        print(f"Could not extract depth from folder name: {sequence_name}")
        return 0, 0, None
    
    # Load transforms
    with open(transforms_file, 'r') as f:
        pose_data = json.load(f)
    
    # Get camera intrinsics
    K = np.array([
        [pose_data['fl_x'], 0, pose_data['cx']],
        [0, pose_data['fl_y'], pose_data['cy']],
        [0, 0, 1]
    ])
    
    # Find center frame and precompute transforms
    frames = pose_data['frames']
    center_idx = len(frames) // 2
    center_pose = np.array(frames[center_idx]['transform_matrix'])
    
    poses = [np.array(frame['transform_matrix']) for frame in frames]
    shared_data, frame_transforms = precompute_transforms(poses, center_pose, K, depth)
    
    # Create output directory
    os.makedirs(os.path.join(output_dir, 'rgb'), exist_ok=True)
    
    # Prepare arguments for multiprocessing
    args_list = []
    for i, frame_transform in enumerate(frame_transforms):
        rgb_path = os.path.join(rgb_dir, f"{i:04d}.png")
        output_path = os.path.join(output_dir, 'rgb', f"{i:04d}.png")
        args_list.append((i, rgb_path, output_path, frame_transform, shared_data))
    
    # Process with multiprocessing
    processed_count = 0
    with ProcessPoolExecutor(max_workers=num_workers) as executor:
        results = list(executor.map(process_single_frame, args_list))
        processed_count = sum(results)
    
    return processed_count, len(frames), depth

def batch_process_render_data(num_workers=None):
    """
    Batch process all render data with refocusing
    使用多进程加速处理
    """
    if num_workers is None:
        num_workers = min(mp.cpu_count(), 16)
    
    base_dir = "/home_ssd/sjy/UE5_Project/PCGBiomeForestPoplar/Saved/MovieRenders/render_data"
    output_base = "./refocus_data"
    
    # First, count total sequences for overall progress
    sequence_list = []
    
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
                continue
            
            # Create output directory path
            output_dir = os.path.join(output_base, trajectory_type, sequence_name)
            
            # Skip if output directory already exists
            if os.path.exists(output_dir):
                continue
            
            sequence_list.append((trajectory_type, sequence_name, transforms_file, rgb_dir, output_dir))
    
    if len(sequence_list) == 0:
        print("No sequences to process (all already exist or missing required files)")
        return
    
    print(f"Found {len(sequence_list)} sequences to process")
    print(f"Using {num_workers} worker processes")
    
    # Process sequences with overall progress
    total_processed = 0
    total_frames = 0
    
    with tqdm(sequence_list, desc="Processing sequences", unit="seq") as pbar:
        for trajectory_type, sequence_name, transforms_file, rgb_dir, output_dir in pbar:
            pbar.set_postfix_str(f"{trajectory_type}/{sequence_name}")
            processed_count, frame_count, depth = process_dataset(
                transforms_file, rgb_dir, output_dir, sequence_name, num_workers
            )
            total_processed += processed_count
            total_frames += frame_count
    
    print(f"✓ Batch processing completed!")
    print(f"  Processed {len(sequence_list)} sequences")
    print(f"  Total frames: {total_processed}/{total_frames}")
    print(f"  Success rate: {total_processed/total_frames*100:.1f}%")

def main():
    if len(sys.argv) == 1:
        # Batch mode
        batch_process_render_data()
    elif len(sys.argv) == 2:
        # Batch mode with custom worker count
        num_workers = int(sys.argv[1])
        batch_process_render_data(num_workers)
    elif len(sys.argv) == 4:
        # Manual mode
        transforms_file = sys.argv[1]
        rgb_dir = sys.argv[2]
        output_dir = sys.argv[3]
        sequence_name = os.path.basename(output_dir)
        process_dataset(transforms_file, rgb_dir, output_dir, sequence_name)
    else:
        print("Usage:")
        print("  Batch mode: python align_poses_to_center.py [num_workers]")
        print("  Manual mode: python align_poses_to_center.py <transforms.json> <rgb_dir> <output_dir>")
        print(f"  Default workers: {min(mp.cpu_count(), 16)}")
        sys.exit(1)

if __name__ == "__main__":
    # 多进程安全保护
    mp.set_start_method('spawn', force=True)
    main()