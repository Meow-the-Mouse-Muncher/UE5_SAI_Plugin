"""
Refocus images using inverse geometric transformation with GPU acceleration and GT depth map
"""

import json
import numpy as np
import sys
import os
os.environ["OPENCV_IO_ENABLE_OPENEXR"] = "1"
os.environ["CUDA_VISIBLE_DEVICES"] = "1"
import cv2
import re
from tqdm import tqdm
from concurrent.futures import ProcessPoolExecutor
import multiprocessing as mp
import torch

def load_depth(depth_path):
    """读取深度图文件，单位是cm"""
    if not os.path.exists(depth_path):
        return None
    
    try:
        return cv2.imread(depth_path, cv2.IMREAD_ANYCOLOR | cv2.IMREAD_ANYDEPTH)[..., 0]
    except Exception as e:
        print(f"Error reading depth file {depth_path}: {e}")
        return None

def precompute_transforms(poses, center_pose, K):
    """预计算所有帧的变换矩阵"""
    K_inv = np.linalg.inv(K)
    R_center = center_pose[:3, :3]
    T_center = center_pose[:3, 3:4]
    
    shared_data = (K, K_inv)
    frame_transforms = []
    
    for pose in poses:
        R_src = pose[:3, :3]
        T_src = pose[:3, 3:4]
        
        # Calculate relative transform: Center -> Source (inverse direction)
        R_c2s = R_src.T @ R_center
        T_c2s = R_src.T @ (T_center - T_src)
        
        frame_transforms.append((R_c2s, T_c2s))
    
    return shared_data, frame_transforms

def refocus_batch_gpu(src_imgs, center_depth, frame_transforms, shared_data, device='cuda'):
    """批量GPU加速重聚焦"""
    K, K_inv = shared_data
    B = len(src_imgs)
    h, w = src_imgs[0].shape[:2]
    
    device = torch.device(device)
    
    # Batch tensors
    src_tensors = torch.from_numpy(np.stack(src_imgs)).float().to(device).permute(0, 3, 1, 2) / 255.0 # [B, 3, H, W]
    depth_m = -torch.from_numpy(center_depth).float().to(device).unsqueeze(0) / 100.0 # [1, H, W]
    
    K_tensor = torch.from_numpy(K).float().to(device)
    K_inv_tensor = torch.from_numpy(K_inv).float().to(device)
    
    # R_c2s and T_c2s stacking
    R_c2s = torch.stack([torch.from_numpy(ft[0]) for ft in frame_transforms]).float().to(device) # [B, 3, 3]
    T_c2s = torch.stack([torch.from_numpy(ft[1]) for ft in frame_transforms]).float().to(device) # [B, 3, 1]
    
    # Create pixel coordinates for center camera
    u, v = torch.meshgrid(torch.arange(w, device=device), torch.arange(h, device=device), indexing='xy')
    pixels = torch.stack([u.flatten(), v.flatten(), torch.ones_like(u).flatten()], dim=0).float() # [3, HW]
    
    # Unproject to rays in center camera coordinate system
    rays_center = (K_inv_tensor @ pixels).reshape(3, h, w) # [3, H, W]
    
    # Apply per-pixel depth and transform to source camera
    points_3d_center = rays_center * depth_m
    points_3d_flat = points_3d_center.reshape(3, -1).unsqueeze(0).expand(B, -1, -1) # [B, 3, HW]
    
    # Transform to source camera: R_c2s @ points_3d_center + T_c2s
    transformed_points = torch.bmm(R_c2s, points_3d_flat) + T_c2s # [B, 3, HW]
    
    # Project to source camera image plane
    projected = torch.bmm(K_tensor.unsqueeze(0).expand(B, -1, -1), transformed_points) # [B, 3, HW]
    projected = projected.reshape(B, 3, h, w)
    
    # Perspective division
    z = projected[:, 2:3, :, :] + 1e-6
    x_coords = projected[:, 0:1, :, :] / z
    y_coords = projected[:, 1:2, :, :] / z
    
    # Normalize coordinates to [-1, 1] for grid_sample
    x_norm = 2.0 * x_coords / (w - 1) - 1.0
    y_norm = 2.0 * y_coords / (h - 1) - 1.0
    
    # Create sampling grid
    grid = torch.cat([x_norm, y_norm], dim=1).permute(0, 2, 3, 1) # [B, H, W, 2]
    
    # Sample color from source
    sampled_colors = torch.nn.functional.grid_sample(
        src_tensors, grid, 
        mode='bilinear', 
        padding_mode='zeros', 
        align_corners=True
    )
    
    return (sampled_colors.permute(0, 2, 3, 1) * 255.0).cpu().numpy().astype(np.uint8)

def process_dataset(transforms_file, rgb_dir, gt_depth_dir, src_depth_dir, output_dir, use_gpu=True, batch_size=4):
    """处理数据集"""
    device = 'cuda' if use_gpu and torch.cuda.is_available() else 'cpu'
    if use_gpu and not torch.cuda.is_available():
        print("CUDA not available, falling back to CPU")
        use_gpu = False
    
    # Load transforms and setup
    with open(transforms_file, 'r') as f:
        pose_data = json.load(f)
    
    K = np.array([
        [pose_data['fl_x'], 0, pose_data['cx']],
        [0, pose_data['fl_y'], pose_data['cy']],
        [0, 0, 1]
    ])
    
    frames = pose_data['frames']
    center_idx = len(frames) // 2
    center_pose = np.array(frames[center_idx]['transform_matrix'])
    poses = [np.array(frame['transform_matrix']) for frame in frames]
    
    # Load center depth map from GT depth directory
    center_depth_path = os.path.join(gt_depth_dir, f"{center_idx:04d}.exr")
    center_depth = load_depth(center_depth_path)
    if center_depth is None:
        print(f"Failed to load center depth map: {center_depth_path}")
        return 0, 0
    
    shared_data, frame_transforms = precompute_transforms(poses, center_pose, K)
    os.makedirs(os.path.join(output_dir, 'rgb'), exist_ok=True)
    
    processed_count = 0
    frame_indices = list(range(len(frames)))
    
    # Copy center frame
    center_rgb_path = os.path.join(rgb_dir, f"{center_idx:04d}.png")
    if os.path.exists(center_rgb_path):
        rgb_img = cv2.imread(center_rgb_path)
        if rgb_img is not None:
            output_path = os.path.join(output_dir, 'rgb', f"{center_idx:04d}.png")
            os.makedirs(os.path.dirname(output_path), exist_ok=True)
            if cv2.imwrite(output_path, rgb_img):
                processed_count += 1

    # Batch process other frames
    other_indices = [i for i in frame_indices if i != center_idx]
    
    for i in range(0, len(other_indices), batch_size):
        batch_idx = other_indices[i:i+batch_size]
        curr_batch_imgs = []
        curr_batch_transforms = []
        curr_batch_paths = []
        
        for idx in batch_idx:
            rgb_path = os.path.join(rgb_dir, f"{idx:04d}.png")
            if os.path.exists(rgb_path):
                img = cv2.imread(rgb_path)
                if img is not None:
                    curr_batch_imgs.append(img)
                    curr_batch_transforms.append(frame_transforms[idx])
                    curr_batch_paths.append(os.path.join(output_dir, 'rgb', f"{idx:04d}.png"))
        
        if curr_batch_imgs:
            results = refocus_batch_gpu(curr_batch_imgs, center_depth, curr_batch_transforms, shared_data, device)
            for res, out_path in zip(results, curr_batch_paths):
                os.makedirs(os.path.dirname(output_path), exist_ok=True)
                if cv2.imwrite(out_path, res):
                    processed_count += 1
    
    return processed_count, len(frames)

def batch_process_render_data(base_dir, output_base, use_gpu=True, batch_size=4):
    """批量处理渲染数据"""
    if use_gpu and not torch.cuda.is_available():
        print("CUDA not available, falling back to CPU")
        use_gpu = False
    
    # Find all sequences (both OCC and GT)
    sequence_list = []
    for trajectory_type in os.listdir(base_dir):
        trajectory_dir = os.path.join(base_dir, trajectory_type)
        if not os.path.isdir(trajectory_dir):
            continue
            
        for sequence_name in os.listdir(trajectory_dir):
            # Process both OCC and GT sequences
            if not (sequence_name.endswith('_occ') or sequence_name.endswith('_GT')):
                continue
                
            sequence_dir = os.path.join(trajectory_dir, sequence_name)
            if not os.path.isdir(sequence_dir):
                continue
            
            # Determine GT depth directory (for center frame) and source depth directory
            if sequence_name.endswith('_occ'):
                # OCC sequence uses GT depth for center, OCC depth for source frames
                gt_sequence_name = sequence_name.replace('_occ', '_GT')
                gt_sequence_dir = os.path.join(trajectory_dir, gt_sequence_name)
                src_depth_dir = os.path.join(sequence_dir, "depth")  # OCC depth for source frames
            else:
                # GT sequence uses its own depth for both center and source frames
                gt_sequence_dir = sequence_dir
                src_depth_dir = os.path.join(sequence_dir, "depth")  # GT depth for source frames
            
            transforms_file = os.path.join(sequence_dir, "pose", "transforms.json")
            rgb_dir = os.path.join(sequence_dir, "rgb")
            gt_depth_dir = os.path.join(gt_sequence_dir, "depth")  # GT depth for center frame
            
            if not all(os.path.exists(p) for p in [transforms_file, rgb_dir, gt_depth_dir, src_depth_dir]):
                continue
            
            output_dir = os.path.join(output_base, trajectory_type, sequence_name)
            if os.path.exists(output_dir):
                continue
            
            sequence_list.append((trajectory_type, sequence_name, transforms_file, rgb_dir, gt_depth_dir, src_depth_dir, output_dir))
    
    if len(sequence_list) == 0:
        print("No sequences to process")
        return
    
    print(f"Found {len(sequence_list)} sequences to process")
    print(f"Using {'GPU' if use_gpu else 'CPU'} acceleration")
    print(f"Processing with Batch Size: {batch_size}")
    print("Using INVERSE projection with GT depth map")
    
    total_processed = 0
    total_frames = 0
    
    with tqdm(sequence_list, desc="Processing sequences", unit="seq") as pbar:
        for trajectory_type, sequence_name, transforms_file, rgb_dir, gt_depth_dir, src_depth_dir, output_dir in pbar:
            pbar.set_postfix_str(f"{trajectory_type}/{sequence_name}")
            processed_count, frame_count = process_dataset(
                transforms_file, rgb_dir, gt_depth_dir, src_depth_dir, output_dir, use_gpu, batch_size
            )
            total_processed += processed_count
            total_frames += frame_count
    
    print(f"✓ Processing completed!")
    print(f"  Sequences: {len(sequence_list)}")
    print(f"  Frames: {total_processed}/{total_frames}")
    print(f"  Success rate: {total_processed/total_frames*100:.1f}%")

def main():
    # ==================== 配置参数 ====================
    # 输入数据路径
    BASE_DIR = "/home_ssd/sjy/UE5_Project/PCGBiomeForestPoplar/Saved/MovieRenders/sparse_data"
    
    # 输出路径
    OUTPUT_BASE = "./refocus_sparse_data"
    
    # 是否使用GPU加速
    USE_GPU = True
    
    # Batch size
    BATCH_SIZE = 128
    
    # ================================================
    
    batch_process_render_data(BASE_DIR, OUTPUT_BASE, USE_GPU, BATCH_SIZE)

if __name__ == "__main__":
    # 多进程安全保护
    mp.set_start_method('spawn', force=True)
    main()
