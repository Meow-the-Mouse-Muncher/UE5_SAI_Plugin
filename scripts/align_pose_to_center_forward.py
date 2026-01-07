"""
Refocus images using forward geometric transformation with GPU acceleration and per-pixel depth
"""

import json
import numpy as np
import sys
import os
os.environ["OPENCV_IO_ENABLE_OPENEXR"] = "1"
import cv2
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
        
        # Calculate relative transform: Source -> Center
        R_s2c = R_center.T @ R_src
        T_s2c = R_center.T @ (T_src - T_center)
        
        frame_transforms.append((R_s2c, T_s2c))
    
    return shared_data, frame_transforms

def refocus_image_gpu(src_img, src_depth, frame_transform, shared_data, device='cuda'):
    """GPU加速的重聚焦图像处理"""
    R_s2c, T_s2c = frame_transform
    K, K_inv = shared_data
    h, w = src_img.shape[:2]
    
    device = torch.device(device if torch.cuda.is_available() else 'cpu')
    
    # Convert to tensors
    src_tensor = torch.from_numpy(src_img).float().to(device)
    depth_m = -torch.from_numpy(src_depth).float().to(device) / 100.0  # cm->m, negative for -Z
    K_tensor = torch.from_numpy(K).float().to(device)
    K_inv_tensor = torch.from_numpy(K_inv).float().to(device)
    R_s2c_tensor = torch.from_numpy(R_s2c).float().to(device)
    T_s2c_tensor = torch.from_numpy(T_s2c).float().to(device)

    
    # Create pixel coordinates and unproject
    u, v = torch.meshgrid(torch.arange(w, device=device), torch.arange(h, device=device), indexing='xy')
    ones = torch.ones_like(u)
    pixels = torch.stack([u.flatten(), v.flatten(), ones.flatten()], dim=0).float()
    rays_src = (K_inv_tensor @ pixels).reshape(3, h, w)
    
    # Apply depth and transform
    points_3d_src = rays_src * depth_m.unsqueeze(0)
    transformed_points = R_s2c_tensor @ points_3d_src.reshape(3, -1) + T_s2c_tensor
    transformed_points = transformed_points.reshape(3, h, w)
    
    # Project to center camera
    projected = K_tensor @ transformed_points.reshape(3, -1)
    projected = projected.reshape(3, h, w)
    
    # Perspective division
    z = projected[2, :, :] + 1e-6
    x_coords = projected[0, :, :] / z
    y_coords = projected[1, :, :] / z
    
    # Combine masks
    valid_projection_mask = (x_coords >= 0) & (x_coords < w) & (y_coords >= 0) & (y_coords < h)
    
    # Use transformed depth for Z-buffer (depth in center camera coordinate system)
    center_depth = transformed_points[2, :, :]  # Z coordinate in center camera space
    
    # Splatting
    output = gpu_splatting_zbuffer(src_tensor, x_coords, y_coords, center_depth, valid_projection_mask, device)
    
    return output.cpu().numpy().astype(np.uint8)

def gpu_splatting_zbuffer(src_img, x_coords, y_coords, src_depths, valid_mask, device):
    """
    GPU向量化splatting操作，使用Z-buffer保留深度值最大的像素（加速版本）
    """
    h, w, c = src_img.shape
    
    # Get valid pixels
    valid_y_idx, valid_x_idx = torch.where(valid_mask)
    if len(valid_y_idx) == 0:
        return torch.zeros_like(src_img)
    
    target_x = x_coords[valid_y_idx, valid_x_idx]
    target_y = y_coords[valid_y_idx, valid_x_idx]
    src_pixels = src_img[valid_y_idx, valid_x_idx]
    pixel_depths = src_depths[valid_y_idx, valid_x_idx]
    
    # Bilinear coordinates
    x0, y0 = torch.floor(target_x).long(), torch.floor(target_y).long()
    x1, y1 = x0 + 1, y0 + 1
    dx, dy = target_x - x0.float(), target_y - y0.float()
    
    # Bilinear weights
    w00 = (1 - dx) * (1 - dy)
    w01 = (1 - dx) * dy
    w10 = dx * (1 - dy)
    w11 = dx * dy
    
    # Output tensors
    output = torch.zeros_like(src_img)
    
    # 收集所有需要处理的点
    all_x_coords = []
    all_y_coords = []
    all_weights = []
    all_pixels = []
    all_depths = []
    
    # 四个角的数据
    corners = [(x0, y0, w00), (x0, y1, w01), (x1, y0, w10), (x1, y1, w11)]
    
    for x_idx, y_idx, weights in corners:
        # 边界检查
        valid = (x_idx >= 0) & (x_idx < w) & (y_idx >= 0) & (y_idx < h)
        if valid.sum() == 0:
            continue
            
        all_x_coords.append(x_idx[valid])
        all_y_coords.append(y_idx[valid])
        all_weights.append(weights[valid])
        all_pixels.append(src_pixels[valid])
        all_depths.append(pixel_depths[valid])
    
    if len(all_x_coords) == 0:
        return output
    
    # 合并所有数据
    all_x = torch.cat(all_x_coords)
    all_y = torch.cat(all_y_coords)
    all_w = torch.cat(all_weights)
    all_p = torch.cat(all_pixels)
    all_d = torch.cat(all_depths)
    
    # 创建线性索引
    linear_idx = all_y * w + all_x
    
    # 使用scatter操作进行Z-buffer测试
    # 首先找到每个位置的最大深度
    max_depths = torch.full((h * w,), float('-inf'), device=device)
    max_depths.scatter_reduce_(0, linear_idx, all_d, reduce='amax', include_self=False)
    
    # 创建mask，只保留深度等于最大深度的像素
    pixel_max_depths = max_depths[linear_idx]
    keep_mask = (all_d >= pixel_max_depths - 1e-6)  # 使用小的容差避免浮点精度问题
    
    if keep_mask.sum() == 0:
        return output
    
    # 过滤数据
    final_x = all_x[keep_mask]
    final_y = all_y[keep_mask]
    final_w = all_w[keep_mask]
    final_p = all_p[keep_mask]
    final_linear_idx = final_y * w + final_x
    
    # 对于有多个相同最大深度的像素，进行加权平均
    weighted_pixels = final_p * final_w.unsqueeze(-1)
    
    # 使用scatter_add进行累加
    output_flat = output.view(-1, c)
    weight_sum = torch.zeros(h * w, device=device)
    
    output_flat.scatter_add_(0, final_linear_idx.unsqueeze(-1).expand(-1, c), weighted_pixels)
    weight_sum.scatter_add_(0, final_linear_idx, final_w)
    
    # 归一化
    valid_weights = weight_sum > 1e-6
    if valid_weights.sum() > 0:
        output_flat[valid_weights] = output_flat[valid_weights] / weight_sum[valid_weights].unsqueeze(-1)
    
    return output

def process_single_frame(args):
    """处理单帧"""
    frame_idx, rgb_path, depth_path, output_path, frame_transform, shared_data, device, center_idx = args
    
    # Skip center frame, just copy it
    if frame_idx == center_idx:
        if os.path.exists(rgb_path):
            rgb_img = cv2.imread(rgb_path)
            if rgb_img is not None:
                os.makedirs(os.path.dirname(output_path), exist_ok=True)
                return cv2.imwrite(output_path, rgb_img)
        return False
    
    if not os.path.exists(rgb_path) or not os.path.exists(depth_path):
        return False
    
    # Load images
    rgb_img = cv2.imread(rgb_path)
    depth_img = load_depth(depth_path)
    if rgb_img is None or depth_img is None:
        return False
    
    # Refocus and save
    refocused_rgb = refocus_image_gpu(rgb_img, depth_img, frame_transform, shared_data, device)
    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    return cv2.imwrite(output_path, refocused_rgb)


def process_dataset(transforms_file, rgb_dir, depth_dir, output_dir, sequence_name, num_workers=None, use_gpu=True):
    """处理数据集"""
    if num_workers is None:
        num_workers = min(mp.cpu_count(), 8) if use_gpu else min(mp.cpu_count(), 16)
    
    device = 'cuda' if use_gpu and torch.cuda.is_available() else 'cpu'
    if use_gpu and not torch.cuda.is_available():
        print("CUDA not available, falling back to CPU")
        use_gpu = False
    
    print(f"Using device: {device}")
    
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
    
    shared_data, frame_transforms = precompute_transforms(poses, center_pose, K)
    os.makedirs(os.path.join(output_dir, 'rgb'), exist_ok=True)
    
    if use_gpu:
        # GPU processing - sequential
        processed_count = 0
        for i, frame_transform in enumerate(frame_transforms):
            rgb_path = os.path.join(rgb_dir, f"{i:04d}.png")
            depth_path = os.path.join(depth_dir, f"{i:04d}.exr")
            output_path = os.path.join(output_dir, 'rgb', f"{i:04d}.png")
            
            if i == center_idx:
                # Copy center frame
                if os.path.exists(rgb_path):
                    rgb_img = cv2.imread(rgb_path)
                    if rgb_img is not None:
                        os.makedirs(os.path.dirname(output_path), exist_ok=True)
                        if cv2.imwrite(output_path, rgb_img):
                            processed_count += 1
            elif os.path.exists(rgb_path) and os.path.exists(depth_path):
                rgb_img = cv2.imread(rgb_path)
                depth_img = load_depth(depth_path)
                if rgb_img is not None and depth_img is not None:
                    refocused_rgb = refocus_image_gpu(rgb_img, depth_img, frame_transform, shared_data, device)
                    os.makedirs(os.path.dirname(output_path), exist_ok=True)
                    if cv2.imwrite(output_path, refocused_rgb):
                        processed_count += 1
    else:
        # CPU multiprocessing
        args_list = []
        for i, frame_transform in enumerate(frame_transforms):
            rgb_path = os.path.join(rgb_dir, f"{i:04d}.png")
            depth_path = os.path.join(depth_dir, f"{i:04d}.exr")
            output_path = os.path.join(output_dir, 'rgb', f"{i:04d}.png")
            args_list.append((i, rgb_path, depth_path, output_path, frame_transform, shared_data, device, center_idx))
        
        with ProcessPoolExecutor(max_workers=num_workers) as executor:
            results = list(executor.map(process_single_frame, args_list))
            processed_count = sum(results)
    
    return processed_count, len(frames)

def batch_process_render_data(num_workers=None, use_gpu=True):
    """批量处理渲染数据"""
    if num_workers is None:
        num_workers = min(mp.cpu_count(), 8) if use_gpu else min(mp.cpu_count(), 16)
    
    base_dir = "/home_ssd/sjy/UE5_Project/PCGBiomeForestPoplar/Saved/MovieRenders/render_data"
    output_base = "./refocus_data_forward" if use_gpu else "./refocus_data_forward"
    
    if use_gpu and not torch.cuda.is_available():
        print("CUDA not available, falling back to CPU")
        use_gpu = False
        output_base = "./refocus_data_forward"
    
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
            depth_dir = os.path.join(sequence_dir, "depth")
            
            if not all(os.path.exists(p) for p in [transforms_file, rgb_dir, depth_dir]):
                continue
            
            output_dir = os.path.join(output_base, trajectory_type, sequence_name)
            if os.path.exists(output_dir):
                continue
            
            sequence_list.append((trajectory_type, sequence_name, transforms_file, rgb_dir, depth_dir, output_dir))
    
    if len(sequence_list) == 0:
        print("No sequences to process")
        return
    
    print(f"Found {len(sequence_list)} sequences to process")
    print(f"Using {'GPU' if use_gpu else 'CPU'} acceleration")
    print("Using FORWARD projection with per-pixel depth")
    
    total_processed = 0
    total_frames = 0
    
    with tqdm(sequence_list, desc="Processing sequences", unit="seq") as pbar:
        for trajectory_type, sequence_name, transforms_file, rgb_dir, depth_dir, output_dir in pbar:
            pbar.set_postfix_str(f"{trajectory_type}/{sequence_name}")
            processed_count, frame_count = process_dataset(
                transforms_file, rgb_dir, depth_dir, output_dir, sequence_name, num_workers, use_gpu
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
    elif len(sys.argv) == 5:
        # Manual mode
        transforms_file, rgb_dir, depth_dir, output_dir = sys.argv[1:5]
        sequence_name = os.path.basename(output_dir)
        process_dataset(transforms_file, rgb_dir, depth_dir, output_dir, sequence_name)
    else:
        print("Usage:")
        print("  GPU batch:  python align_pose_to_center_forward.py")
        print("  CPU batch:  python align_pose_to_center_forward.py cpu")
        print("  Workers:    python align_pose_to_center_forward.py [num_workers]")
        print("  Manual:     python align_pose_to_center_forward.py <transforms.json> <rgb_dir> <depth_dir> <output_dir>")
        print("  Note: Uses per-pixel depth maps for accurate warping")
        sys.exit(1)

if __name__ == "__main__":
    # 多进程安全保护
    mp.set_start_method('spawn', force=True)
    main()