"""
Refocus images to a specific depth plane using forward geometric transformation
(Source camera -> Center camera projection) with GPU acceleration
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
import torch
import torch.nn.functional as F

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
    预计算所有帧的变换矩阵，避免重复计算 (Forward projection)
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
        
        # Calculate relative transform: Source -> Center
        R_s2c = R_center.T @ R_src
        T_s2c = R_center.T @ (T_src - T_center)
        
        frame_transforms.append((R_s2c, T_s2c))
    
    return shared_data, frame_transforms

def refocus_image_fast_forward_gpu(src_img, frame_transform, shared_data, device='cuda'):
    """
    使用GPU加速的快速重聚焦图像 (Forward projection)
    """
    R_s2c, T_s2c = frame_transform
    K, K_inv, depth = shared_data
    h, w = src_img.shape[:2]
    
    # Convert to torch tensors and move to GPU
    device = torch.device(device if torch.cuda.is_available() else 'cpu')
    
    src_tensor = torch.from_numpy(src_img).float().to(device)
    K_tensor = torch.from_numpy(K).float().to(device)
    K_inv_tensor = torch.from_numpy(K_inv).float().to(device)
    R_s2c_tensor = torch.from_numpy(R_s2c).float().to(device)
    T_s2c_tensor = torch.from_numpy(T_s2c).float().to(device)
    
    # Create pixel coordinates
    u, v = torch.meshgrid(torch.arange(w, device=device), torch.arange(h, device=device), indexing='xy')
    ones = torch.ones_like(u)
    pixels = torch.stack([u.flatten(), v.flatten(), ones.flatten()], dim=0).float()
    
    # Unproject source pixels to rays
    rays_src = K_inv_tensor @ pixels
    
    # Transform from source to center camera coordinate system
    transformed_points = R_s2c_tensor @ rays_src + T_s2c_tensor / depth
    
    # Project to center camera image plane
    projected = K_tensor @ transformed_points
    
    # Perspective division
    z = projected[2, :] + 1e-6
    x_coords = (projected[0, :] / z).reshape(h, w)
    y_coords = (projected[1, :] / z).reshape(h, w)
    
    # Use GPU-accelerated splatting for forward mapping
    output = optimized_splatting_gpu(src_tensor, x_coords, y_coords, device)
    
    # Convert back to numpy
    return output.cpu().numpy().astype(np.uint8)

def optimized_splatting_gpu(src_img, x_coords, y_coords, device):
    """
    更优化的GPU splatting，使用向量化操作
    """
    h, w, c = src_img.shape
    
    # Create valid mask
    valid_mask = (x_coords >= 0) & (x_coords < w) & (y_coords >= 0) & (y_coords < h)
    
    # Get valid coordinates and pixels
    valid_y_idx, valid_x_idx = torch.where(valid_mask)
    if len(valid_y_idx) == 0:
        return torch.zeros_like(src_img)
    
    target_x = x_coords[valid_y_idx, valid_x_idx]
    target_y = y_coords[valid_y_idx, valid_x_idx]
    src_pixels = src_img[valid_y_idx, valid_x_idx]  # [N, 3]
    
    # Bilinear coordinates
    x0 = torch.floor(target_x).long()
    y0 = torch.floor(target_y).long()
    x1 = x0 + 1
    y1 = y0 + 1
    
    dx = target_x - x0.float()
    dy = target_y - y0.float()
    
    # Weights
    w00 = (1 - dx) * (1 - dy)
    w01 = (1 - dx) * dy
    w10 = dx * (1 - dy)
    w11 = dx * dy
    
    # Create output tensors
    output = torch.zeros_like(src_img)
    weight_map = torch.zeros(h, w, device=device)
    
    # Vectorized bounds checking and splatting
    def safe_splat(x_idx, y_idx, weights, pixels):
        valid = (x_idx >= 0) & (x_idx < w) & (y_idx >= 0) & (y_idx < h)
        if valid.sum() == 0:
            return
        
        valid_x = x_idx[valid]
        valid_y = y_idx[valid]
        valid_w = weights[valid]
        valid_p = pixels[valid]
        
        # Use index_add for atomic-like operations
        flat_idx = valid_y * w + valid_x
        output.view(-1, c).index_add_(0, flat_idx, valid_w.unsqueeze(-1) * valid_p)
        weight_map.view(-1).index_add_(0, flat_idx, valid_w)
    
    # Splat to four corners
    safe_splat(x0, y0, w00, src_pixels)
    safe_splat(x0, y1, w01, src_pixels)
    safe_splat(x1, y0, w10, src_pixels)
    safe_splat(x1, y1, w11, src_pixels)
    
    # Normalize
    weight_mask = weight_map > 1e-6
    output[weight_mask] = output[weight_mask] / weight_map[weight_mask].unsqueeze(-1)
    
    return output

def process_single_frame_gpu(args):
    """
    处理单帧的工作函数，用于GPU加速
    """
    frame_idx, rgb_path, output_path, frame_transform, shared_data, device = args
    
    if not os.path.exists(rgb_path):
        return False
    
    # Load image
    rgb_img = cv2.imread(rgb_path)
    if rgb_img is None:
        return False
    
    # Refocus using GPU-accelerated forward projection
    refocused_rgb = refocus_image_fast_forward_gpu(rgb_img, frame_transform, shared_data, device)
    
    # Save
    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    cv2.imwrite(output_path, refocused_rgb)
    
    return True

def refocus_image_forward(src_img, src_pose, center_pose, K, depth):
    """
    Refocus source image to specified depth plane using forward projection.
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
    
    # 2. Calculate Relative Transform: Source -> Center
    # 公式推导：
    # P_center = R_center^T * (P_world - T_center)
    # P_world = R_src * P_src + T_src
    # 代入得: P_center = R_center^T * (R_src * P_src + T_src - T_center)
    #                  = (R_center^T @ R_src) * P_src + R_center^T @ (T_src - T_center)
    
    R_s2c = R_center.T @ R_src                        # 相对旋转
    T_s2c = R_center.T @ (T_src - T_center)           # 相对平移
    
    # 3. Create pixel coordinates for source camera
    u, v = np.meshgrid(np.arange(w), np.arange(h))
    ones = np.ones_like(u)
    
    # Stack to homogeneous coordinates [3, H*W]
    pixels = np.stack([u.flatten(), v.flatten(), ones.flatten()], axis=0)
    
    # 4. Unproject to rays in source camera coordinate system
    rays_src = K_inv @ pixels  # [3, H*W]
    
    # 5. Apply depth and transform to center camera
    # 原理: P_src = rays_src * depth
    #       P_center = R_s2c @ P_src + T_s2c
    #                = R_s2c @ (rays_src * depth) + T_s2c
    #       投影时我们只需要比例关系，所以两边同时除以 depth
    #       P_center_normalized = R_s2c @ rays_src + T_s2c / depth
    
    transformed_points = R_s2c @ rays_src + T_s2c / depth
    
    # 6. Project back to center image coordinates
    projected = K @ transformed_points
    
    # 7. Normalize by Z coordinate (Perspective Division) and reshape
    z = projected[2, :] + 1e-6
    x_coords = (projected[0, :] / z).reshape(h, w)
    y_coords = (projected[1, :] / z).reshape(h, w)
    
    # Debug info (Optional)
    # print(f"Forward projection bounds: X[{x_coords.min():.1f}, {x_coords.max():.1f}], Y[{y_coords.min():.1f}, {y_coords.max():.1f}]")
    
    # 8. Create output using forward mapping
    return create_output_image_forward(src_img, x_coords, y_coords, h, w)

def process_dataset(transforms_file, rgb_dir, output_dir, sequence_name, num_workers=None, use_gpu=True):
    """
    Process dataset: refocus images to the depth plane specified in folder name
    使用GPU加速处理 (Forward projection)
    """
    if num_workers is None:
        num_workers = min(mp.cpu_count(), 8) if use_gpu else min(mp.cpu_count(), 16)
    
    # Check GPU availability
    device = 'cuda' if use_gpu and torch.cuda.is_available() else 'cpu'
    if use_gpu and not torch.cuda.is_available():
        print("CUDA not available, falling back to CPU")
        use_gpu = False
    
    print(f"Using device: {device}")
    
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
    
    if use_gpu:
        # GPU processing - use threading instead of multiprocessing to share GPU memory
        processed_count = 0
        with tqdm(range(len(frame_transforms)), desc="Processing frames") as pbar:
            for i, frame_transform in enumerate(frame_transforms):
                rgb_path = os.path.join(rgb_dir, f"{i:04d}.png")
                output_path = os.path.join(output_dir, 'rgb', f"{i:04d}.png")
                
                if os.path.exists(rgb_path):
                    rgb_img = cv2.imread(rgb_path)
                    if rgb_img is not None:
                        refocused_rgb = refocus_image_fast_forward_gpu(rgb_img, frame_transform, shared_data, device)
                        os.makedirs(os.path.dirname(output_path), exist_ok=True)
                        cv2.imwrite(output_path, refocused_rgb)
                        processed_count += 1
                
                pbar.update(1)
    else:
        # CPU multiprocessing fallback
        args_list = []
        for i, frame_transform in enumerate(frame_transforms):
            rgb_path = os.path.join(rgb_dir, f"{i:04d}.png")
            output_path = os.path.join(output_dir, 'rgb', f"{i:04d}.png")
            args_list.append((i, rgb_path, output_path, frame_transform, shared_data, device))
        
        processed_count = 0
        with ProcessPoolExecutor(max_workers=num_workers) as executor:
            results = list(executor.map(process_single_frame_gpu, args_list))
            processed_count = sum(results)
    
    return processed_count, len(frames), depth

def batch_process_render_data(num_workers=None, use_gpu=True):
    """
    Batch process all render data with GPU-accelerated forward refocusing
    """
    if num_workers is None:
        num_workers = min(mp.cpu_count(), 8) if use_gpu else min(mp.cpu_count(), 16)
    
    base_dir = "/home_ssd/sjy/UE5_Project/PCGBiomeForestPoplar/Saved/MovieRenders/render_data"
    output_base = "./refocus_data_forward_gpu" if use_gpu else "./refocus_data_forward"
    
    # Check GPU availability
    if use_gpu and not torch.cuda.is_available():
        print("CUDA not available, falling back to CPU")
        use_gpu = False
        output_base = "./refocus_data_forward"
    
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
    print(f"Using {'GPU' if use_gpu else 'CPU'} acceleration")
    print(f"Using {num_workers} worker processes")
    print("Using FORWARD projection method")
    
    # Process sequences with overall progress
    total_processed = 0
    total_frames = 0
    
    with tqdm(sequence_list, desc="Processing sequences", unit="seq") as pbar:
        for trajectory_type, sequence_name, transforms_file, rgb_dir, output_dir in pbar:
            pbar.set_postfix_str(f"{trajectory_type}/{sequence_name}")
            processed_count, frame_count, depth = process_dataset(
                transforms_file, rgb_dir, output_dir, sequence_name, num_workers, use_gpu
            )
            total_processed += processed_count
            total_frames += frame_count
    
    print(f"✓ Batch processing completed!")
    print(f"  Processed {len(sequence_list)} sequences")
    print(f"  Total frames: {total_processed}/{total_frames}")
    print(f"  Success rate: {total_processed/total_frames*100:.1f}%")

def main():
    if len(sys.argv) == 1:
        # Batch mode with GPU
        batch_process_render_data()
    elif len(sys.argv) == 2:
        arg = sys.argv[1]
        if arg.lower() == 'cpu':
            # CPU-only mode
            batch_process_render_data(use_gpu=False)
        elif arg.isdigit():
            # Batch mode with custom worker count
            num_workers = int(arg)
            batch_process_render_data(num_workers)
        else:
            print("Invalid argument. Use 'cpu' for CPU-only mode or a number for worker count.")
            sys.exit(1)
    elif len(sys.argv) == 3:
        if sys.argv[1].lower() == 'cpu':
            # CPU mode with custom worker count
            num_workers = int(sys.argv[2])
            batch_process_render_data(num_workers, use_gpu=False)
        else:
            print("Invalid arguments.")
            sys.exit(1)
    elif len(sys.argv) == 4:
        # Manual mode
        transforms_file = sys.argv[1]
        rgb_dir = sys.argv[2]
        output_dir = sys.argv[3]
        sequence_name = os.path.basename(output_dir)
        process_dataset(transforms_file, rgb_dir, output_dir, sequence_name)
    else:
        print("Usage:")
        print("  GPU batch mode: python align_pose_to_center_forward.py")
        print("  CPU batch mode: python align_pose_to_center_forward.py cpu")
        print("  Custom workers: python align_pose_to_center_forward.py [num_workers]")
        print("  CPU + workers:  python align_pose_to_center_forward.py cpu [num_workers]")
        print("  Manual mode:    python align_pose_to_center_forward.py <transforms.json> <rgb_dir> <output_dir>")
        print(f"  Default workers: GPU={min(mp.cpu_count(), 8)}, CPU={min(mp.cpu_count(), 16)}")
        sys.exit(1)

if __name__ == "__main__":
    # 多进程安全保护
    mp.set_start_method('spawn', force=True)
    main()