"""
Refocus images using inverse geometric transformation with GPU acceleration and GT depth map
"""

import json
import numpy as np
import sys
import os
os.environ["OPENCV_IO_ENABLE_OPENEXR"] = "1"
os.environ["CUDA_VISIBLE_DEVICES"] = "0"
import cv2
import re
import h5py
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

def refocus_image_gpu(src_img, src_depth, center_depth, frame_transform, shared_data, device='cuda'):
    """GPU加速的逆向重聚焦图像处理，使用GT深度图和深度剔除"""
    R_c2s, T_c2s = frame_transform
    K, K_inv = shared_data
    h, w = src_img.shape[:2]
    
    device = torch.device(device if torch.cuda.is_available() else 'cpu')
    
    # Convert to tensors
    src_tensor = torch.from_numpy(src_img).float().to(device)
    # z_measured = torch.from_numpy(src_depth).float().to(device) / 100.0  # cm->m
    depth_m = -torch.from_numpy(center_depth).float().to(device) / 100.0  # cm->m, negative for -Z
    K_tensor = torch.from_numpy(K).float().to(device)
    K_inv_tensor = torch.from_numpy(K_inv).float().to(device)
    R_c2s_tensor = torch.from_numpy(R_c2s).float().to(device)
    T_c2s_tensor = torch.from_numpy(T_c2s).float().to(device)
    
    # Create pixel coordinates for center camera
    u, v = torch.meshgrid(torch.arange(w, device=device), torch.arange(h, device=device), indexing='xy')
    ones = torch.ones_like(u)
    pixels = torch.stack([u.flatten(), v.flatten(), ones.flatten()], dim=0).float()
    
    # Unproject to rays in center camera coordinate system
    rays_center = (K_inv_tensor @ pixels).reshape(3, h, w)  # [3, H, W]
    
    # Apply per-pixel depth and transform to source camera
    # points_3d_center = rays_center * depth
    points_3d_center = rays_center * depth_m.unsqueeze(0)
    
    # Transform to source camera: R_c2s @ points_3d_center + T_c2s
    transformed_points = R_c2s_tensor @ points_3d_center.reshape(3, -1) + T_c2s_tensor
    transformed_points = transformed_points.reshape(3, h, w)
    
    # Project to source camera image plane
    projected = K_tensor @ transformed_points.reshape(3, -1)
    projected = projected.reshape(3, h, w)
    
    # Perspective division
    z = projected[2, :, :] + 1e-6
    x_coords = projected[0, :, :] / z
    y_coords = projected[1, :, :] / z
    
    # Get projected depth (Z_proj) - distance from source camera
    # z_proj = -transformed_points[2, :, :]   
    
    # Use GPU grid sampling for remapping
    # Normalize coordinates to [-1, 1] for grid_sample
    x_norm = 2.0 * x_coords / (w - 1) - 1.0
    y_norm = 2.0 * y_coords / (h - 1) - 1.0
    
    # Create sampling grid
    grid = torch.stack([x_norm, y_norm], dim=-1).unsqueeze(0)  # [1, H, W, 2]
    src_tensor_norm = src_tensor.permute(2, 0, 1).unsqueeze(0) / 255.0  # [1, 3, H, W]
    # Sample color and depth from source
    sampled_color = torch.nn.functional.grid_sample(
        src_tensor_norm, grid, 
        mode='bilinear', 
        padding_mode='zeros', 
        align_corners=True
    )
    
    # Depth culling: Z_proj < Z_measured means occlusion

    # depth_mask = z_proj < z_measured  # Keep pixels where projected depth >= measured depth
    
    # Apply depth mask
    result = sampled_color.squeeze(0).permute(1, 2, 0) * 255.0  # [H, W, 3]
    # result[depth_mask] = 0  # Set occluded pixels to black
    
    return result.cpu().numpy().astype(np.uint8)



def process_dataset(transforms_file, rgb_dir, gt_rgb_dir, gt_depth_dir, src_depth_dir, h5_path, use_gpu=True):
    """处理数据集并保存为H5"""
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
        return False

    # Load center RGB from GT directory (Ground Truth Target)
    center_gt_rgb_path = os.path.join(gt_rgb_dir, f"{center_idx:04d}.png")
    center_gt_rgb = cv2.imread(center_gt_rgb_path)
    center_gt_rgb = cv2.cvtColor(center_gt_rgb, cv2.COLOR_BGR2RGB)

    h, w = center_gt_rgb.shape[:2]
    
    shared_data, frame_transforms = precompute_transforms(poses, center_pose, K)
    
    # Prepare data containers
    refocused_imgs = np.zeros((len(frames), h, w, 3), dtype=np.uint8)
    occ_center_rgb = None
    
    # GPU processing - sequential
    processed_count = 0
    for i, frame_transform in enumerate(frame_transforms):
        rgb_path = os.path.join(rgb_dir, f"{i:04d}.png")
        
        if not os.path.exists(rgb_path):
            continue

        rgb_img = cv2.imread(rgb_path)
        rgb_img = cv2.cvtColor(rgb_img, cv2.COLOR_BGR2RGB)
        if rgb_img is None:
            continue
        
        if i == center_idx:
            # Copy center frame (identity transform)
            occ_center_rgb = rgb_img.copy()
            refocused_imgs[i] = rgb_img
            processed_count += 1
        elif os.path.exists(rgb_path):
            # Load corresponding source depth map
            src_depth_path = os.path.join(src_depth_dir, f"{i:04d}.exr")
            src_depth = load_depth(src_depth_path)
            
            if src_depth is not None:
                refocused_rgb = refocus_image_gpu(rgb_img, src_depth, center_depth, frame_transform, shared_data, device)
                refocused_imgs[i] = refocused_rgb
                processed_count += 1
                
    # Save to H5
    os.makedirs(os.path.dirname(h5_path), exist_ok=True)
    try:
        with h5py.File(h5_path, 'w') as f:
            # --- GT Group ---
            gt_g = f.create_group('GT')
            gt_g.create_dataset('rgb', data=center_gt_rgb)

            # --- occ_ref Group ---
            ref_g = f.create_group('occ_ref')
            # 排除中间帧，因为它会保存在 occ_center 中
            ref_imgs_filtered = np.delete(refocused_imgs, center_idx, axis=0)
            ref_g.create_dataset('rgb', data=ref_imgs_filtered)


            # --- occ_center Group ---
            center_g = f.create_group('occ_center')
            if occ_center_rgb is not None:
                center_g.create_dataset('rgb', data=occ_center_rgb)
            else:
                center_g.create_dataset('rgb', data=refocused_imgs[center_idx])
            
        return True
    except Exception as e:
        print(f"Error saving {h5_path}: {e}")
        if os.path.exists(h5_path):
            os.remove(h5_path)
        return False

def batch_process_render_data(base_dir, output_base, use_gpu=True):
    """批量处理渲染数据"""
    if use_gpu and not torch.cuda.is_available():
        print("CUDA not available, falling back to CPU")
        use_gpu = False
    
    # Find all sequences (Start from OCC and find matching GT)
    sequence_list = []
    
    if os.path.exists(base_dir):
        trajectories = [d for d in os.listdir(base_dir) if os.path.isdir(os.path.join(base_dir, d))]
    else:
        print(f"Base dir {base_dir} does not exist")
        return

    for trajectory_type in trajectories:
        trajectory_dir = os.path.join(base_dir, trajectory_type)
            
        for sequence_name in os.listdir(trajectory_dir):
            # We focus on OCC sequences to fix them
            if not sequence_name.endswith('_occ'):
                continue
            
            occ_sequence_dir = os.path.join(trajectory_dir, sequence_name)
            if not os.path.isdir(occ_sequence_dir):
                continue
            
            # Find matching GT sequence
            gt_sequence_name = sequence_name.replace('_occ', '_GT')
            gt_sequence_dir = os.path.join(trajectory_dir, gt_sequence_name)
            
            if not os.path.isdir(gt_sequence_dir):
                continue
            
            # OCC Inputs
            transforms_file = os.path.join(occ_sequence_dir, "pose", "transforms.json")
            rgb_dir = os.path.join(occ_sequence_dir, "rgb")
            src_depth_dir = os.path.join(occ_sequence_dir, "depth") # OCC depth for source frames refocusing
            
            # GT Inputs
            gt_rgb_dir = os.path.join(gt_sequence_dir, "rgb") # New: Needed for GT target
            gt_depth_dir = os.path.join(gt_sequence_dir, "depth") # GT depth for center frame (and target)
            
            if not all(os.path.exists(p) for p in [transforms_file, rgb_dir, src_depth_dir, gt_rgb_dir, gt_depth_dir]):
                continue
            
            # Output H5 path
            base_name = sequence_name.replace('_occ', '')
            h5_output_path = os.path.join(output_base, trajectory_type, f"{base_name}.h5")
            
            if os.path.exists(h5_output_path):
                continue
            
            sequence_list.append((
                transforms_file, 
                rgb_dir, 
                gt_rgb_dir,
                gt_depth_dir, 
                src_depth_dir, 
                h5_output_path
            ))
    
    if len(sequence_list) == 0:
        print("No sequences to process")
        return
    
    print(f"Found {len(sequence_list)} sequences to process")
    print(f"Using {'GPU' if use_gpu else 'CPU'} acceleration")
    
    success_count = 0
    
    with tqdm(sequence_list, desc="Processing sequences", unit="seq") as pbar:
        for args in pbar:
            h5_path = args[-1]
            pbar.set_description(f"Processing {os.path.basename(h5_path)}")
            # args: transforms_file, rgb_dir, gt_rgb_dir, gt_depth_dir, src_depth_dir, h5_path
            result = process_dataset(*args, use_gpu)
            if result:
                success_count += 1
    
    print(f"✓ Processing completed!")
    print(f"  Sequences: {len(sequence_list)}")
    print(f"  Successful: {success_count}")

def main():
    # ==================== 配置参数 ====================
    BASE_DIR = "/home_ssd/sjy/UE5_Project/PCGBiomeForestPoplar/Saved/MovieRenders/train_data"
    
    OUTPUT_BASE = "/home_ssd/sjy/deocc_swin/train_data"
    
    # 是否使用GPU加速
    USE_GPU = True
    
    # ================================================
    
    batch_process_render_data(BASE_DIR, OUTPUT_BASE, USE_GPU)

if __name__ == "__main__":
    # 多进程安全保护
    mp.set_start_method('spawn', force=True)
    main()
