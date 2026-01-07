"""
Refocus images using inverse geometric transformation with GPU acceleration and depth plane
"""

import json
import numpy as np
import sys
import os
import cv2
import re
from tqdm import tqdm
from concurrent.futures import ProcessPoolExecutor
import multiprocessing as mp
import torch

def extract_depth_from_folder_name(folder_name):
    """从文件夹名提取深度值，单位是米"""
    match = re.search(r'height_(\d+)', folder_name)
    if match:
        # negative for -Z axis (camera coordinate system)
        return -int(match.group(1)) 
    return None

def precompute_transforms(poses, center_pose, K, depth):
    """预计算所有帧的变换矩阵"""
    K_inv = np.linalg.inv(K)
    R_center = center_pose[:3, :3]
    T_center = center_pose[:3, 3:4]
    
    shared_data = (K, K_inv, depth)
    frame_transforms = []
    
    for pose in poses:
        R_src = pose[:3, :3]
        T_src = pose[:3, 3:4]
        
        # Calculate relative transform: Center -> Source (inverse direction)
        R_c2s = R_src.T @ R_center
        T_c2s = R_src.T @ (T_center - T_src)
        
        frame_transforms.append((R_c2s, T_c2s))
    
    return shared_data, frame_transforms

def refocus_image_gpu(src_img, frame_transform, shared_data, device='cuda'):
    """GPU加速的逆向重聚焦图像处理，使用深度平面"""
    R_c2s, T_c2s = frame_transform
    K, K_inv, depth = shared_data
    h, w = src_img.shape[:2]
    
    device = torch.device(device if torch.cuda.is_available() else 'cpu')
    
    # Convert to tensors
    src_tensor = torch.from_numpy(src_img).float().to(device)
    K_tensor = torch.from_numpy(K).float().to(device)
    K_inv_tensor = torch.from_numpy(K_inv).float().to(device)
    R_c2s_tensor = torch.from_numpy(R_c2s).float().to(device)
    T_c2s_tensor = torch.from_numpy(T_c2s).float().to(device)
    
    # Create pixel coordinates for center camera
    u, v = torch.meshgrid(torch.arange(w, device=device), torch.arange(h, device=device), indexing='xy')
    ones = torch.ones_like(u)
    pixels = torch.stack([u.flatten(), v.flatten(), ones.flatten()], dim=0).float()
    
    # Unproject to rays in center camera coordinate system
    rays_center = K_inv_tensor @ pixels  # [3, H*W]
    
    # Apply depth plane and transform to source camera
    # R_c2s @ rays_center + T_c2s / depth
    transformed_points = R_c2s_tensor @ rays_center + T_c2s_tensor / depth
    
    # Project to source camera image plane
    projected = K_tensor @ transformed_points
    
    # Perspective division
    z = projected[2, :] + 1e-6
    x_coords = (projected[0, :] / z).reshape(h, w)
    y_coords = (projected[1, :] / z).reshape(h, w)
    

    
    # Use GPU grid sampling for remapping
    # Normalize coordinates to [-1, 1] for grid_sample
    x_norm = 2.0 * x_coords / (w - 1) - 1.0
    y_norm = 2.0 * y_coords / (h - 1) - 1.0

    
    # Create sampling grid
    grid = torch.stack([x_norm, y_norm], dim=-1).unsqueeze(0)  # [1, H, W, 2]
    
    # Prepare source image for grid sampling
    src_tensor_norm = src_tensor.permute(2, 0, 1).unsqueeze(0) / 255.0  # [1, 3, H, W]

    
    # Sample using grid_sample
    sampled = torch.nn.functional.grid_sample(
        src_tensor_norm, grid, 
        mode='bilinear', 
        padding_mode='zeros', 
        align_corners=True
    )
    

    
    # Convert back to [H, W, 3] format
    result = sampled.squeeze(0).permute(1, 2, 0) * 255.0

    
    # Check for valid pixels
    valid_pixels = (result > 0).sum()
    total_pixels = result.numel()

    
    return result.cpu().numpy().astype(np.uint8)

def process_single_frame(args):
    """处理单帧"""
    frame_idx, rgb_path, output_path, frame_transform, shared_data, device, center_idx = args
    
    # Skip center frame, just copy it
    if frame_idx == center_idx:
        if os.path.exists(rgb_path):
            rgb_img = cv2.imread(rgb_path)
            if rgb_img is not None:
                os.makedirs(os.path.dirname(output_path), exist_ok=True)
                return cv2.imwrite(output_path, rgb_img)
        return False
    
    if not os.path.exists(rgb_path):
        return False
    
    # Load image
    rgb_img = cv2.imread(rgb_path)
    if rgb_img is None:
        return False
    
    # Refocus using GPU
    refocused_rgb = refocus_image_gpu(rgb_img, frame_transform, shared_data, device)
    
    # Save
    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    return cv2.imwrite(output_path, refocused_rgb)

def process_dataset(transforms_file, rgb_dir, output_dir, sequence_name, num_workers=None, use_gpu=True):
    """处理数据集"""
    if num_workers is None:
        num_workers = min(mp.cpu_count(), 16) if use_gpu else min(mp.cpu_count(), 16)
    
    device = 'cuda' if use_gpu and torch.cuda.is_available() else 'cpu'
    if use_gpu and not torch.cuda.is_available():
        print("CUDA not available, falling back to CPU")
        use_gpu = False
    
    print(f"Using device: {device}")
    
    # Extract depth from sequence name
    depth = extract_depth_from_folder_name(sequence_name)
    if depth is None:
        print(f"Could not extract depth from folder name: {sequence_name}")
        return 0, 0
    
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
    
    shared_data, frame_transforms = precompute_transforms(poses, center_pose, K, depth)
    os.makedirs(os.path.join(output_dir, 'rgb'), exist_ok=True)
    
    if use_gpu:
        # GPU processing - sequential
        processed_count = 0
        for i, frame_transform in enumerate(frame_transforms):
            rgb_path = os.path.join(rgb_dir, f"{i:04d}.png")
            output_path = os.path.join(output_dir, 'rgb', f"{i:04d}.png")
            
            if i == center_idx:
                # Copy center frame
                if os.path.exists(rgb_path):
                    rgb_img = cv2.imread(rgb_path)
                    if rgb_img is not None:
                        os.makedirs(os.path.dirname(output_path), exist_ok=True)
                        if cv2.imwrite(output_path, rgb_img):
                            processed_count += 1
            elif os.path.exists(rgb_path):
                rgb_img = cv2.imread(rgb_path)
                if rgb_img is not None:
                    refocused_rgb = refocus_image_gpu(rgb_img, frame_transform, shared_data, device)
                    os.makedirs(os.path.dirname(output_path), exist_ok=True)
                    if cv2.imwrite(output_path, refocused_rgb):
                        processed_count += 1
    else:
        # CPU multiprocessing
        args_list = []
        for i, frame_transform in enumerate(frame_transforms):
            rgb_path = os.path.join(rgb_dir, f"{i:04d}.png")
            output_path = os.path.join(output_dir, 'rgb', f"{i:04d}.png")
            args_list.append((i, rgb_path, output_path, frame_transform, shared_data, device, center_idx))
        
        with ProcessPoolExecutor(max_workers=num_workers) as executor:
            results = list(executor.map(process_single_frame, args_list))
            processed_count = sum(results)
    
    return processed_count, len(frames)

def batch_process_render_data(num_workers=None, use_gpu=True):
    """批量处理渲染数据"""
    if num_workers is None:
        num_workers = min(mp.cpu_count(), 16) if use_gpu else min(mp.cpu_count(), 16)
    
    base_dir = "/home_ssd/sjy/UE5_Project/PCGBiomeForestPoplar/Saved/MovieRenders/render_data"
    output_base = "./refocus_data" if use_gpu else "./refocus_data"
    
    if use_gpu and not torch.cuda.is_available():
        print("CUDA not available, falling back to CPU")
        use_gpu = False
        output_base = "./refocus_data"
    
    # Find sequences
    sequence_list = []
    for trajectory_type in os.listdir(base_dir):
        trajectory_dir = os.path.join(base_dir, trajectory_type)
        if not os.path.isdir(trajectory_dir):
            continue
            
        for sequence_name in os.listdir(trajectory_dir):
            sequence_dir = os.path.join(trajectory_dir, sequence_name)
            if not os.path.isdir(sequence_dir):
                continue
                
            transforms_file = os.path.join(sequence_dir, "pose", "transforms.json")
            rgb_dir = os.path.join(sequence_dir, "rgb")
            
            if not all(os.path.exists(p) for p in [transforms_file, rgb_dir]):
                continue
            
            output_dir = os.path.join(output_base, trajectory_type, sequence_name)
            if os.path.exists(output_dir):
                continue
            
            sequence_list.append((trajectory_type, sequence_name, transforms_file, rgb_dir, output_dir))
    
    if len(sequence_list) == 0:
        print("No sequences to process")
        return
    
    print(f"Found {len(sequence_list)} sequences to process")
    print(f"Using {'GPU' if use_gpu else 'CPU'} acceleration")
    print("Using INVERSE projection with depth plane")
    
    total_processed = 0
    total_frames = 0
    
    with tqdm(sequence_list, desc="Processing sequences", unit="seq") as pbar:
        for trajectory_type, sequence_name, transforms_file, rgb_dir, output_dir in pbar:
            pbar.set_postfix_str(f"{trajectory_type}/{sequence_name}")
            processed_count, frame_count = process_dataset(
                transforms_file, rgb_dir, output_dir, sequence_name, num_workers, use_gpu
            )
            total_processed += processed_count
            total_frames += frame_count
    
    print(f"✓ Processing completed!")
    print(f"  Sequences: {len(sequence_list)}")
    print(f"  Frames: {total_processed}/{total_frames}")
    print(f"  Success rate: {total_processed/total_frames*100:.1f}%")

def main():
    if len(sys.argv) == 1:
        batch_process_render_data()
    elif len(sys.argv) == 2:
        arg = sys.argv[1]
        if arg.lower() == 'cpu':
            batch_process_render_data(use_gpu=False)
        elif arg.isdigit():
            batch_process_render_data(int(arg))
        else:
            print("Invalid argument. Use 'cpu' or number for worker count.")
            sys.exit(1)
    elif len(sys.argv) == 3 and sys.argv[1].lower() == 'cpu':
        batch_process_render_data(int(sys.argv[2]), use_gpu=False)
    elif len(sys.argv) == 4:
        # Manual mode
        transforms_file, rgb_dir, output_dir = sys.argv[1:4]
        sequence_name = os.path.basename(output_dir)
        process_dataset(transforms_file, rgb_dir, output_dir, sequence_name)
    else:
        print("Usage:")
        print("  GPU batch:  python align_pose_to_center_inv.py")
        print("  CPU batch:  python align_pose_to_center_inv.py cpu")
        print("  Workers:    python align_pose_to_center_inv.py [num_workers]")
        print("  Manual:     python align_pose_to_center_inv.py <transforms.json> <rgb_dir> <output_dir>")
        print("  Note: Uses depth plane extracted from folder name")
        sys.exit(1)

if __name__ == "__main__":
    # 多进程安全保护
    mp.set_start_method('spawn', force=True)
    main()
