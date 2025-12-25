#!/usr/bin/env python3
"""
Analyze camera poses from transforms.json file
Extract rotation and translation information and visualize in 3D
"""

import json
import numpy as np
import sys
import os
from scipy.spatial.transform import Rotation as R
import matplotlib.pyplot as plt
from mpl_toolkits.mplot3d import Axes3D

def rotation_matrix_to_euler(rot_matrix):
    """
    Convert rotation matrix to Euler angles (in degrees)
    Returns roll, pitch, yaw
    """
    r = R.from_matrix(rot_matrix)
    euler = r.as_euler('xyz', degrees=True)
    return euler

def analyze_single_pose(transform_matrix, frame_idx):
    """
    Analyze a single pose matrix
    """
    matrix = np.array(transform_matrix)
    
    # Extract rotation (3x3) and translation (3x1)
    rotation = matrix[:3, :3]
    translation = matrix[:3, 3]
    
    # Convert rotation to Euler angles
    euler_angles = rotation_matrix_to_euler(rotation)
    
    print(f"Frame {frame_idx:3d}:")
    print(f"  Translation: [{translation[0]:8.3f}, {translation[1]:8.3f}, {translation[2]:8.3f}]")
    print(f"  Rotation (deg): [{euler_angles[0]:6.1f}, {euler_angles[1]:6.1f}, {euler_angles[2]:6.1f}] (roll, pitch, yaw)")
    
    return {
        'translation': translation,
        'rotation_matrix': rotation,
        'euler_angles': euler_angles
    }

def analyze_pose_sequence(poses_data):
    """
    Analyze the entire pose sequence in world coordinates
    """
    frames = poses_data['frames']
    
    print(f"Analyzing {len(frames)} poses in world coordinates")
    print("="*70)
    
    all_translations = []
    all_euler_angles = []
    
    for i, frame in enumerate(frames):
        current_matrix = np.array(frame['transform_matrix'])
        
        # Extract rotation and translation
        rotation = current_matrix[:3, :3]
        translation = current_matrix[:3, 3]
        euler = rotation_matrix_to_euler(rotation)
        
        print(f"Frame {i:3d}:")
        print(f"  Translation: [{translation[0]:8.3f}, {translation[1]:8.3f}, {translation[2]:8.3f}]")
        print(f"  Rotation (deg): [{euler[0]:6.1f}, {euler[1]:6.1f}, {euler[2]:6.1f}] (roll, pitch, yaw)")
        
        all_translations.append(translation)
        all_euler_angles.append(euler)
    
    # Calculate statistics
    all_translations = np.array(all_translations)
    all_euler_angles = np.array(all_euler_angles)
    
    print("\n" + "="*70)
    print("WORLD COORDINATE ANALYSIS:")
    print("="*70)
    
    print("Translation ranges:")
    print(f"  X: [{all_translations[:, 0].min():8.3f}, {all_translations[:, 0].max():8.3f}] (range: {all_translations[:, 0].max() - all_translations[:, 0].min():6.3f})")
    print(f"  Y: [{all_translations[:, 1].min():8.3f}, {all_translations[:, 1].max():8.3f}] (range: {all_translations[:, 1].max() - all_translations[:, 1].min():6.3f})")
    print(f"  Z: [{all_translations[:, 2].min():8.3f}, {all_translations[:, 2].max():8.3f}] (range: {all_translations[:, 2].max() - all_translations[:, 2].min():6.3f})")
    
    print("\nRotation ranges (degrees):")
    print(f"  Roll:  [{all_euler_angles[:, 0].min():6.1f}, {all_euler_angles[:, 0].max():6.1f}] (range: {all_euler_angles[:, 0].max() - all_euler_angles[:, 0].min():5.1f})")
    print(f"  Pitch: [{all_euler_angles[:, 1].min():6.1f}, {all_euler_angles[:, 1].max():6.1f}] (range: {all_euler_angles[:, 1].max() - all_euler_angles[:, 1].min():5.1f})")
    print(f"  Yaw:   [{all_euler_angles[:, 2].min():6.1f}, {all_euler_angles[:, 2].max():6.1f}] (range: {all_euler_angles[:, 2].max() - all_euler_angles[:, 2].min():5.1f})")
    
    # Calculate trajectory length
    trajectory_length = 0
    for i in range(1, len(all_translations)):
        trajectory_length += np.linalg.norm(all_translations[i] - all_translations[i-1])
    
    print(f"\nTRAJECTORY ANALYSIS:")
    print(f"  Total trajectory length: {trajectory_length:.3f} units")
    print(f"  Average step size: {trajectory_length/(len(all_translations)-1):.3f} units")
    
    print(f"\nCamera intrinsics:")
    print(f"  FOV X: {poses_data.get('camera_angle_x', 'N/A')} radians")
    print(f"  Focal length X: {poses_data.get('fl_x', 'N/A')}")
    print(f"  Focal length Y: {poses_data.get('fl_y', 'N/A')}")
    print(f"  Resolution: {poses_data.get('w', 'N/A')} x {poses_data.get('h', 'N/A')}")

def visualize_camera_poses(poses_data, save_path=None):
    """
    Visualize camera poses in 3D space with camera frustums
    """
    frames = poses_data['frames']
    
    # Extract camera positions and orientations
    positions = []
    orientations = []
    
    for frame in frames:
        matrix = np.array(frame['transform_matrix'])
        position = matrix[:3, 3]
        rotation = matrix[:3, :3]
        
        positions.append(position)
        orientations.append(rotation)
    
    positions = np.array(positions)
    
    # Create 3D plot
    fig = plt.figure(figsize=(15, 12))
    ax = fig.add_subplot(111, projection='3d')
    
    # Plot camera trajectory
    ax.plot(positions[:, 0], positions[:, 1], positions[:, 2], 
            'b-', alpha=0.5, linewidth=2, label='Camera trajectory')
    
    # Draw camera frustums
    for i, (pos, rot) in enumerate(zip(positions, orientations)):
        # Camera frustum parameters
        frustum_size = 0.05
        frustum_depth = 0.1
        
        # Define frustum corners in camera coordinate system (-Z-axis forward)
        corners = np.array([
            [-frustum_size, -frustum_size, -frustum_depth],  # far bottom left
            [frustum_size, -frustum_size, -frustum_depth],   # far bottom right
            [frustum_size, frustum_size, -frustum_depth],    # far top right
            [-frustum_size, frustum_size, -frustum_depth],   # far top left
            [0, 0, 0]  # camera center
        ])
        
        # Transform corners to world coordinates
        world_corners = (rot @ corners.T).T + pos
        
        # Color based on frame number
        color = plt.cm.viridis(i / len(positions))
        
        # Draw frustum edges
        # From camera center to corners
        for j in range(4):
            ax.plot([world_corners[4, 0], world_corners[j, 0]],
                   [world_corners[4, 1], world_corners[j, 1]],
                   [world_corners[4, 2], world_corners[j, 2]], 
                   color=color, alpha=0.6, linewidth=1)
        
        # Draw frustum rectangle
        for j in range(4):
            next_j = (j + 1) % 4
            ax.plot([world_corners[j, 0], world_corners[next_j, 0]],
                   [world_corners[j, 1], world_corners[next_j, 1]],
                   [world_corners[j, 2], world_corners[next_j, 2]], 
                   color=color, alpha=0.6, linewidth=1)
        
        # Draw camera coordinate axes (every 3rd camera for clarity)
        if i % 3 == 0:
            axis_scale = 0.3
            
            # Z-axis (blue) - camera viewing direction
            z_end = pos + rot[:, 2] * axis_scale  
            ax.plot([pos[0], z_end[0]], [pos[1], z_end[1]], [pos[2], z_end[2]], 
                   'b-', alpha=0.8, linewidth=3)
            
            # X-axis (red)
            x_end = pos + rot[:, 0] * axis_scale
            ax.plot([pos[0], x_end[0]], [pos[1], x_end[1]], [pos[2], x_end[2]], 
                   'r-', alpha=0.8, linewidth=2)
            
            # Y-axis (green)
            y_end = pos + rot[:, 1] * axis_scale
            ax.plot([pos[0], y_end[0]], [pos[1], y_end[1]], [pos[2], y_end[2]], 
                   'g-', alpha=0.8, linewidth=2)
    
    # Mark start and end positions with larger frustums
    start_pos = positions[0]
    start_rot = orientations[0]
    end_pos = positions[-1]
    end_rot = orientations[-1]
    
    # Draw larger frustum for start (green) - (-Z-axis forward)
    frustum_size = 0.08
    frustum_depth = 0.15
    corners = np.array([
        [-frustum_size, -frustum_size, -frustum_depth],
        [frustum_size, -frustum_size, -frustum_depth],
        [frustum_size, frustum_size, -frustum_depth],
        [-frustum_size, frustum_size, -frustum_depth],
        [0, 0, 0]
    ])
    
    world_corners = (start_rot @ corners.T).T + start_pos
    for j in range(4):
        ax.plot([world_corners[4, 0], world_corners[j, 0]],
               [world_corners[4, 1], world_corners[j, 1]],
               [world_corners[4, 2], world_corners[j, 2]], 
               'g-', alpha=0.9, linewidth=3)
    for j in range(4):
        next_j = (j + 1) % 4
        ax.plot([world_corners[j, 0], world_corners[next_j, 0]],
               [world_corners[j, 1], world_corners[next_j, 1]],
               [world_corners[j, 2], world_corners[next_j, 2]], 
               'g-', alpha=0.9, linewidth=3)
    
    # Draw larger frustum for end (red)
    world_corners = (end_rot @ corners.T).T + end_pos
    for j in range(4):
        ax.plot([world_corners[4, 0], world_corners[j, 0]],
               [world_corners[4, 1], world_corners[j, 1]],
               [world_corners[4, 2], world_corners[j, 2]], 
               'r-', alpha=0.9, linewidth=3)
    for j in range(4):
        next_j = (j + 1) % 4
        ax.plot([world_corners[j, 0], world_corners[next_j, 0]],
               [world_corners[j, 1], world_corners[next_j, 1]],
               [world_corners[j, 2], world_corners[next_j, 2]], 
               'r-', alpha=0.9, linewidth=3)
    
    # Mark center frame with special frustum (orange)
    center_idx = len(positions) // 2
    center_pos = positions[center_idx]
    center_rot = orientations[center_idx]
    
    world_corners = (center_rot @ corners.T).T + center_pos
    for j in range(4):
        ax.plot([world_corners[4, 0], world_corners[j, 0]],
               [world_corners[4, 1], world_corners[j, 1]],
               [world_corners[4, 2], world_corners[j, 2]], 
               'orange', alpha=0.9, linewidth=3)
    for j in range(4):
        next_j = (j + 1) % 4
        ax.plot([world_corners[j, 0], world_corners[next_j, 0]],
               [world_corners[j, 1], world_corners[next_j, 1]],
               [world_corners[j, 2], world_corners[next_j, 2]], 
               'orange', alpha=0.9, linewidth=3)
    
    # Add legend
    ax.plot([], [], 'g-', linewidth=3, label='Start camera')
    ax.plot([], [], 'r-', linewidth=3, label='End camera')
    ax.plot([], [], 'orange', linewidth=3, label='Center camera')
    ax.plot([], [], 'b-', alpha=0.5, linewidth=2, label='Camera trajectory')
    
    # Set labels and title
    ax.set_xlabel('X')
    ax.set_ylabel('Y')
    ax.set_zlabel('Z')
    ax.set_title('Camera Poses with Viewing Directions')
    ax.legend()
    
    # Make axes equal
    max_range = np.array([positions[:, 0].max() - positions[:, 0].min(),
                         positions[:, 1].max() - positions[:, 1].min(),
                         positions[:, 2].max() - positions[:, 2].min()]).max() / 2.0
    
    mid_x = (positions[:, 0].max() + positions[:, 0].min()) * 0.5
    mid_y = (positions[:, 1].max() + positions[:, 1].min()) * 0.5
    mid_z = (positions[:, 2].max() + positions[:, 2].min()) * 0.5
    
    ax.set_xlim(mid_x - max_range, mid_x + max_range)
    ax.set_ylim(mid_y - max_range, mid_y + max_range)
    ax.set_zlim(mid_z - max_range, mid_z + max_range)
    
    # Add colorbar for frame numbers
    sm = plt.cm.ScalarMappable(cmap='viridis', norm=plt.Normalize(vmin=0, vmax=len(positions)-1))
    sm.set_array([])
    cbar = plt.colorbar(sm, ax=ax, shrink=0.5, aspect=20)
    cbar.set_label('Frame Number')
    
    if save_path:
        plt.savefig(save_path, dpi=300, bbox_inches='tight')
        print(f"Visualization saved to: {save_path}")
    
    plt.show()

def main():
    if len(sys.argv) < 2:
        print("Usage: python analyze_poses.py <transforms.json> [--save-plot output.png]")
        print("Example: python analyze_poses.py Saved/MovieRenders/render_data/fix_line/scene_001_Target_001_height_030_GT/pose/transforms.json")
        sys.exit(1)
    
    transforms_file = sys.argv[1]
    save_plot = None
    
    # Check for save plot option
    if len(sys.argv) >= 4 and sys.argv[2] == '--save-plot':
        save_plot = sys.argv[3]
    
    if not os.path.exists(transforms_file):
        print(f"File not found: {transforms_file}")
        sys.exit(1)
    
    # Load transforms
    with open(transforms_file, 'r') as f:
        poses_data = json.load(f)
    
    print(f"Analyzing poses from: {transforms_file}")
    print(f"File: {os.path.basename(transforms_file)}")
    
    analyze_pose_sequence(poses_data)
    
    print("\n" + "="*70)
    print("GENERATING 3D VISUALIZATION...")
    print("="*70)
    
    visualize_camera_poses(poses_data, save_plot)

if __name__ == "__main__":
    main()