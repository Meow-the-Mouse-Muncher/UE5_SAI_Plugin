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
    # Set backend if no display
    if not save_path and os.environ.get('DISPLAY', '') == '':
        print("No DISPLAY environment variable. Using Agg backend.")
        plt.switch_backend('Agg')

    frames = poses_data['frames']
    
    # Extract camera positions and orientations
    positions = []
    
    # Check if we have valid frames
    if not frames:
        print("Warning: No frames found in pose data.")
        return

    for frame in frames:
        matrix = np.array(frame['transform_matrix'])
        position = matrix[:3, 3]
        positions.append(position)
    
    positions = np.array(positions)
    mean_pos = np.mean(positions, axis=0) if len(positions) > 0 else np.array([0,0,0])

    # Dynamic scaling for visualization
    max_range = np.array([
        positions[:, 0].max() - positions[:, 0].min(),
        positions[:, 1].max() - positions[:, 1].min(),
        positions[:, 2].max() - positions[:, 2].min()
    ]).max() / 2.0
    
    frustum_scale = max(max_range * 0.1, 10.0)  # At least 10 units, or 10% of scale
    
    orientations = []

    for frame in frames:
        matrix = np.array(frame['transform_matrix'])
        rotation = matrix[:3, :3]
        orientations.append(rotation)
    
    # Create 3D plot
    fig = plt.figure(figsize=(15, 12))
    ax = fig.add_subplot(111, projection='3d')
    
    # Plot camera trajectory
    ax.plot(positions[:, 0], positions[:, 1], positions[:, 2], 
            'b-', alpha=0.5, linewidth=2, label='Camera trajectory')
    
    # Draw camera frustums
    for i, (pos, rot) in enumerate(zip(positions, orientations)):
        # Camera frustum parameters - now dynamic based on scene scale
        frustum_size = frustum_scale * 0.5
        frustum_depth = frustum_scale
        
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
        
        # Draw camera coordinate axes (FOR EVERY CAMERA now, to debug orientation)
        axis_scale = frustum_scale * 0.8  # Make axes visible based on scene scale
        
        # X-axis (Red) - Right
        x_end = pos + rot[:, 0] * axis_scale
        ax.plot([pos[0], x_end[0]], [pos[1], x_end[1]], [pos[2], x_end[2]], 
               'r-', alpha=0.9, linewidth=1.5)
        
        # Y-axis (Green) - Down (in OpenCV) / Up (in OpenGL)??? 
        # Let's just trust the matrix columns: Col 0=X, Col 1=Y, Col 2=Z
        y_end = pos + rot[:, 1] * axis_scale
        ax.plot([pos[0], y_end[0]], [pos[1], y_end[1]], [pos[2], y_end[2]], 
               'g-', alpha=0.9, linewidth=1.5)
        
        # Z-axis (Blue) - Forward (in OpenCV usually +Z, or -Z?)
        # Standard: Rot matrix columns are the world direction of camera axes.
        # So rot[:, 2] is the camera's Z axis direction in world space.
        z_end = pos + rot[:, 2] * axis_scale  
        ax.plot([pos[0], z_end[0]], [pos[1], z_end[1]], [pos[2], z_end[2]], 
               'b-', alpha=0.9, linewidth=1.5)

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

if __name__ == "__main__":
    import argparse
    import glob

    parser = argparse.ArgumentParser(description="Analyze and visualize camera poses from transforms.json")
    parser.add_argument("path", nargs="?", help="Path to transforms.json file or directory containing it")
    parser.add_argument("--save", "-s", action="store_true", help="Save visualization to file instead of showing")
    parser.add_argument("--output", "-o", help="Specific output path for visualization")
    
    args = parser.parse_args()
    
    target_path = args.path
    
    # Auto-detect logic
    if not target_path:
        # Priority search paths
        potential_base_paths = [
            "Saved/MovieRenders/test_data",
            "Saved/MovieRenders/train_data",
            "Saved/MovieRenders",
        ]
        
        found_files = []
        for base in potential_base_paths:
            if os.path.exists(base):
                print(f"Searching in {base}...")
                found_files.extend(glob.glob(os.path.join(base, "**", "transforms.json"), recursive=True))
                # Stop if we found something reasonable to avoid scanning too much? 
                # No, let's gather options.
        
        # Remove duplicates if any
        found_files = list(set(found_files))
        found_files.sort()

        if not found_files:
            print("No transforms.json found in standard directories.")
            sys.exit(1)
            
        print(f"\nFound {len(found_files)} pose files:")
        for i, f in enumerate(found_files):
            print(f"  [{i}] {f}")
        
        try:
            choice = input(f"\nSelect file [0-{len(found_files)-1}]: ")
            idx = int(choice)
            target_path = found_files[idx]
        except (ValueError, IndexError):
            print("Invalid selection.")
            sys.exit(1)
            
    elif os.path.isdir(target_path):
        # If directory provided, look for transforms.json inside
        potential_path = os.path.join(target_path, "transforms.json")
        # Direct check
        if os.path.exists(potential_path):
            target_path = potential_path
        else:
            # Recursive search in dir (find first)
            print(f"Searching for transforms.json inside directory: {target_path}...")
            found = glob.glob(os.path.join(target_path, "**", "transforms.json"), recursive=True)
            if found:
                target_path = found[0]
                print(f"Found: {target_path}")
            else:
                print(f"No transforms.json found in {target_path}")
                sys.exit(1)

    if not os.path.exists(target_path):
        print(f"File not found: {target_path}")
        sys.exit(1)

    # Load transforms
    with open(target_path, 'r') as f:
        poses_data = json.load(f)
    
    print(f"\nAnalyzing: {target_path}")
    analyze_pose_sequence(poses_data)
    
    print("\n" + "="*70)
    print("GENERATING 3D VISUALIZATION...")
    
    # Logic for save vs show
    save_path = args.output
    if args.save and not save_path:
        # Default save path: same dir as json, named trajectory_vis.png
        save_path = os.path.join(os.path.dirname(target_path), "trajectory_vis.png")
    
    # If no display, force save
    if not save_path and os.environ.get('DISPLAY', '') == '':
         print("No DISPLAY available, forcing save to 'trajectory_vis.png'")
         save_path = os.path.join( "trajectory_vis.png")

    visualize_camera_poses(poses_data, save_path)