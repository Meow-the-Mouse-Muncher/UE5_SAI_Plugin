#!/usr/bin/env python3
"""
Refocus images using triangular mesh projection from source to center camera
"""

import json
import numpy as np
import sys
import os
os.environ["OPENCV_IO_ENABLE_OPENEXR"] = "1"
import cv2
import re
from pathlib import Path
from tqdm import tqdm
from concurrent.futures import ProcessPoolExecutor
import multiprocessing as mp

def load_depth(depth_path):
    """
    读取深度图文件
    Args:
        depth_path: 深度图文件路径
    Returns:
        image: 深度图数组 单位是cm
    """
    if not os.path.exists(depth_path):
        return None, None
    
    try:
        # 使用cv2读取深度图
        image = cv2.imread(depth_path, cv2.IMREAD_ANYCOLOR | cv2.IMREAD_ANYDEPTH)[..., 0]  # (H, W)
        
        return image
        
    except Exception as e:
        print(f"Error reading depth file {depth_path}: {e}")
        return None


def precompute_mesh_transforms(poses, center_pose, K):
    """
    预计算三角网格投影所需的变换矩阵
    """
    K_inv = np.linalg.inv(K)
    R_center = center_pose[:3, :3]
    T_center = center_pose[:3, 3:4]
    
    # 共享数据：相机内参和中心相机参数
    shared_data = {
        'K': K,
        'K_inv': K_inv,
        'R_center': R_center,
        'T_center': T_center
    }
    
    # 每帧的相对变换矩阵
    frame_transforms = []
    for pose in poses:
        R_src = pose[:3, :3]
        T_src = pose[:3, 3:4]
        
        # 计算相对变换：源相机 -> 世界 -> 中心相机
        # P_center = R_center^T * (R_src * P_src + T_src - T_center)
        R_relative = R_center.T @ R_src
        T_relative = R_center.T @ (T_src - T_center)
        
        frame_transforms.append({
            'R_relative': R_relative,
            'T_relative': T_relative,
            'R_src': R_src,
            'T_src': T_src
        })
    
    return shared_data, frame_transforms

def create_mesh_from_depth(depth_img, K, subsample_factor=4):
    """
    从深度图创建三角网格
    Args:
        depth_img: 深度图 (H, W)
        K: 相机内参矩阵
        subsample_factor: 下采样因子，减少三角形数量
    Returns:
        vertices: 3D顶点坐标 (N, 3)
        faces: 三角形面片索引 (M, 3)
        uv_coords: UV纹理坐标 (N, 2)
    """
    h, w = depth_img.shape
    
    # 下采样以减少计算量
    h_sub = h // subsample_factor
    w_sub = w // subsample_factor
    
    # 创建下采样的像素网格
    u_indices = np.arange(0, w, subsample_factor)[:w_sub]
    v_indices = np.arange(0, h, subsample_factor)[:h_sub]
    u_grid, v_grid = np.meshgrid(u_indices, v_indices)
    
    # 获取对应的深度值
    depth_sub = depth_img[v_grid, u_grid]
    
    # 转换深度单位
    depths_m = depth_sub / 100.0  # 厘米->米
    
    # 反投影到3D空间
    K_inv = np.linalg.inv(K)
    ones = np.ones_like(u_grid)
    pixels = np.stack([u_grid, v_grid, ones], axis=-1)  # (h_sub, w_sub, 3)
    
    # 批量反投影
    rays = np.einsum('ij,hwj->hwi', K_inv, pixels)  # (h_sub, w_sub, 3)
    vertices_3d = rays * depths_m[..., np.newaxis]  # (h_sub, w_sub, 3)
    vertices_3d[..., 2] = -vertices_3d[..., 2]  # 将Z坐标变为负数，符合相机朝向-Z的约定
    
    # 重塑为顶点列表
    vertices = vertices_3d.reshape(-1, 3)  # (h_sub*w_sub, 3)
    
    # 创建UV坐标（归一化的图像坐标）
    uv_coords = np.stack([u_grid.flatten() / (w-1), v_grid.flatten() / (h-1)], axis=1)
    
    # 创建三角形面片（使用简单的网格连接）
    faces = []
    
    for i in range(h_sub - 1):
        for j in range(w_sub - 1):
            # 四个顶点的索引
            idx_tl = i * w_sub + j          # top-left
            idx_tr = i * w_sub + (j + 1)    # top-right
            idx_bl = (i + 1) * w_sub + j    # bottom-left
            idx_br = (i + 1) * w_sub + (j + 1)  # bottom-right
            
            # 两个三角形组成一个四边形
            faces.extend([
                [idx_tl, idx_tr, idx_bl],
                [idx_tr, idx_br, idx_bl]
            ])
    
    faces = np.array(faces)
    
    return vertices, faces, uv_coords

def project_mesh_to_target(vertices, faces, uv_coords, src_img, transform, shared_data, target_shape):
    """
    将三角网格投影到目标相机视角
    """
    R_relative = transform['R_relative']
    T_relative = transform['T_relative']
    K = shared_data['K']
    
    target_h, target_w = target_shape
    
    # 变换顶点到中心相机坐标系
    vertices_center = (R_relative @ vertices.T + T_relative).T  # (N, 3)
    
    # 投影到中心相机图像平面
    vertices_proj = (K @ vertices_center.T).T  # (N, 3)
    
    # 透视除法
    z_coords = vertices_proj[:, 2] + 1e-6
    x_coords = vertices_proj[:, 0] / z_coords
    y_coords = vertices_proj[:, 1] / z_coords
    
    # 过滤在图像范围内的顶点
    valid_mask = (x_coords >= 0) & (x_coords < target_w) & (y_coords >= 0) & (y_coords < target_h)
    
    # 创建输出图像
    projected_img = np.zeros((target_h, target_w, 3), dtype=np.uint8)
    
    # 对每个三角形进行光栅化
    for face in faces:
        # 检查三角形的三个顶点是否都在图像范围内
        if not all(valid_mask[face]):
            continue
            
        # 获取三角形在目标图像中的投影坐标
        triangle_2d = np.array([[x_coords[face[i]], y_coords[face[i]]] for i in range(3)])
        
        # 获取对应的UV坐标
        triangle_uv = uv_coords[face]
        
        # 使用改进的重心坐标进行纹理映射
        rasterize_triangle_improved(projected_img, triangle_2d, triangle_uv, src_img)
    
    return projected_img

def rasterize_triangle_improved(target_img, triangle_2d, triangle_uv, src_img):
    """
    改进的三角形光栅化，使用双线性插值采样
    """
    h, w = target_img.shape[:2]
    src_h, src_w = src_img.shape[:2]
    
    # 获取三角形的边界框
    min_x = max(0, int(np.floor(triangle_2d[:, 0].min())))
    max_x = min(w-1, int(np.ceil(triangle_2d[:, 0].max())))
    min_y = max(0, int(np.floor(triangle_2d[:, 1].min())))
    max_y = min(h-1, int(np.ceil(triangle_2d[:, 1].max())))
    
    if min_x >= max_x or min_y >= max_y:
        return
    
    # 遍历边界框内的每个像素
    for y in range(min_y, max_y + 1):
        for x in range(min_x, max_x + 1):
            # 计算重心坐标
            p = np.array([x, y], dtype=np.float32)
            
            # 使用向量叉积计算重心坐标
            v0 = triangle_2d[2] - triangle_2d[0]
            v1 = triangle_2d[1] - triangle_2d[0]
            v2 = p - triangle_2d[0]
            
            dot00 = np.dot(v0, v0)
            dot01 = np.dot(v0, v1)
            dot02 = np.dot(v0, v2)
            dot11 = np.dot(v1, v1)
            dot12 = np.dot(v1, v2)
            
            # 计算重心坐标
            inv_denom = 1 / (dot00 * dot11 - dot01 * dot01 + 1e-8)
            u = (dot11 * dot02 - dot01 * dot12) * inv_denom
            v = (dot00 * dot12 - dot01 * dot02) * inv_denom
            
            # 检查点是否在三角形内
            if u >= 0 and v >= 0 and u + v <= 1:
                w = 1 - u - v
                
                # 插值UV坐标
                uv = w * triangle_uv[0] + u * triangle_uv[1] + v * triangle_uv[2]
                
                # 映射到源图像坐标（浮点数）
                src_x_f = uv[0] * (src_w - 1)
                src_y_f = uv[1] * (src_h - 1)
                
                # 双线性插值采样
                src_x0 = int(np.floor(src_x_f))
                src_x1 = min(src_x0 + 1, src_w - 1)
                src_y0 = int(np.floor(src_y_f))
                src_y1 = min(src_y0 + 1, src_h - 1)
                
                # 插值权重
                wx = src_x_f - src_x0
                wy = src_y_f - src_y0
                
                # 边界检查
                if 0 <= src_x0 < src_w and 0 <= src_y0 < src_h:
                    # 双线性插值
                    c00 = src_img[src_y0, src_x0].astype(np.float32)
                    c01 = src_img[src_y0, src_x1].astype(np.float32)
                    c10 = src_img[src_y1, src_x0].astype(np.float32)
                    c11 = src_img[src_y1, src_x1].astype(np.float32)
                    
                    # 插值计算
                    c0 = c00 * (1 - wx) + c01 * wx
                    c1 = c10 * (1 - wx) + c11 * wx
                    color = c0 * (1 - wy) + c1 * wy
                    
                    target_img[y, x] = color.astype(np.uint8)

def refocus_image_with_mesh(src_img, src_depth, transform, shared_data, target_shape, subsample_factor=2):
    """
    使用三角网格投影进行重聚焦
    Args:
        subsample_factor: 下采样因子，1=最高质量，2=中等质量，4=快速处理
    """
    K = shared_data['K']
    
    # 从深度图创建三角网格
    vertices, faces, uv_coords = create_mesh_from_depth(src_depth, K, subsample_factor)
    
    # 投影网格到目标视角
    projected_img = project_mesh_to_target(vertices, faces, uv_coords, src_img, transform, shared_data, target_shape)
    
    return projected_img

def process_single_frame_with_mesh(args):
    """
    处理单帧的工作函数，使用三角网格投影进行重聚焦
    """
    frame_idx, rgb_path, depth_path, output_path, transform, shared_data, target_shape, subsample_factor = args
    
    if not os.path.exists(rgb_path) or not os.path.exists(depth_path):
        return False
    
    # Load RGB image
    rgb_img = cv2.imread(rgb_path)
    if rgb_img is None:
        return False
    
    # Load depth image
    depth_img = load_depth(depth_path)
    if depth_img is None:
        return False
    
    # Refocus with triangular mesh projection
    refocused_rgb = refocus_image_with_mesh(rgb_img, depth_img, transform, shared_data, target_shape, subsample_factor)
    
    # Save
    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    cv2.imwrite(output_path, refocused_rgb)
    
    return True

def process_dataset(transforms_file, rgb_dir, depth_dir, output_dir, sequence_name, num_workers=None, subsample_factor=2):
    """
    Process dataset: refocus images using triangular mesh projection
    """
    if num_workers is None:
        num_workers = min(mp.cpu_count(), 16)
    
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
    shared_data, frame_transforms = precompute_mesh_transforms(poses, center_pose, K)
    
    # Load a sample image to get target shape
    sample_rgb_path = os.path.join(rgb_dir, "0000.png")
    if os.path.exists(sample_rgb_path):
        sample_img = cv2.imread(sample_rgb_path)
        target_shape = (sample_img.shape[0], sample_img.shape[1])
    else:
        target_shape = (1080, 1920)  # 默认尺寸
    
    # Create output directory
    os.makedirs(os.path.join(output_dir, 'rgb'), exist_ok=True)
    
    # Prepare arguments for multiprocessing
    args_list = []
    for i, transform in enumerate(frame_transforms):
        rgb_path = os.path.join(rgb_dir, f"{i:04d}.png")
        depth_path = os.path.join(depth_dir, f"{i:04d}.exr")
        output_path = os.path.join(output_dir, 'rgb', f"{i:04d}.png")
        args_list.append((i, rgb_path, depth_path, output_path, transform, shared_data, target_shape, subsample_factor))
    
    # Process with multiprocessing
    processed_count = 0
    with ProcessPoolExecutor(max_workers=num_workers) as executor:
        results = list(executor.map(process_single_frame_with_mesh, args_list))
        processed_count = sum(results)
    
    return processed_count, len(frames)

def batch_process_render_data(num_workers, subsample_factor, base_dir, output_base):
    """
    Batch process all render data with triangular mesh projection
    """
    # Find all sequences
    sequence_list = []
    
    for trajectory_type in os.listdir(base_dir):
        trajectory_dir = os.path.join(base_dir, trajectory_type)
        if not os.path.isdir(trajectory_dir):
            continue
            
        for sequence_name in os.listdir(trajectory_dir):
            sequence_dir = os.path.join(trajectory_dir, sequence_name)
            if not os.path.isdir(sequence_dir):
                continue
                
            # Check for required files (including depth)
            transforms_file = os.path.join(sequence_dir, "pose", "transforms.json")
            rgb_dir = os.path.join(sequence_dir, "rgb")
            depth_dir = os.path.join(sequence_dir, "depth")
            
            if not all(os.path.exists(p) for p in [transforms_file, rgb_dir, depth_dir]):
                print(f"Skipping {sequence_name}: missing required files (rgb, depth, or pose)")
                continue
            
            # Create output directory path
            output_dir = os.path.join(output_base, trajectory_type, sequence_name)
            
            # Skip if output directory already exists
            if os.path.exists(output_dir):
                continue
            
            sequence_list.append((trajectory_type, sequence_name, transforms_file, rgb_dir, depth_dir, output_dir))
    
    if len(sequence_list) == 0:
        print("No sequences to process (all already exist or missing required files)")
        return
    
    print(f"Found {len(sequence_list)} sequences to process")
    print(f"Using {num_workers} worker processes")
    print(f"Using triangular mesh projection with subsample_factor={subsample_factor}")
    
    # Process sequences
    total_processed = 0
    total_frames = 0
    
    with tqdm(sequence_list, desc="Processing sequences", unit="seq") as pbar:
        for trajectory_type, sequence_name, transforms_file, rgb_dir, depth_dir, output_dir in pbar:
            pbar.set_postfix_str(f"{trajectory_type}/{sequence_name}")
            processed_count, frame_count = process_dataset(
                transforms_file, rgb_dir, depth_dir, output_dir, sequence_name, num_workers, subsample_factor
            )
            total_processed += processed_count
            total_frames += frame_count
    
    print(f"✓ Triangular mesh refocusing completed!")
    print(f"  Processed {len(sequence_list)} sequences")
    print(f"  Total frames: {total_processed}/{total_frames}")
    print(f"  Success rate: {total_processed/total_frames*100:.1f}%")

def main():
    # ========== 配置参数 ==========
    subsample_factor = 1       # 下采样因子：1=最高质量，2=中等质量，4=快速处理
    num_workers = 16           # 工作进程数
    base_dir = "/home_ssd/sjy/UE5_Project/PCGBiomeForestPoplar/Saved/MovieRenders/render_data"
    output_base = "./refocus_data"
    # ============================
    batch_process_render_data(num_workers, subsample_factor, base_dir, output_base)

if __name__ == "__main__":
    # 多进程安全保护
    mp.set_start_method('spawn', force=True)
    main()