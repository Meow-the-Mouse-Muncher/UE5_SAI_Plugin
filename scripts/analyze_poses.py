#!/usr/bin/env python3
"""
Analyze camera poses from transforms.json file
Extract rotation and translation information
"""

import json
import numpy as np
import sys
import os
from scipy.spatial.transform import Rotation as R

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
    Analyze the entire pose sequence relative to center frame
    """
    frames = poses_data['frames']
    
    print(f"Analyzing {len(frames)} poses")
    print("="*70)
    
    # Find center frame
    center_idx = len(frames) // 2
    center_matrix = np.array(frames[center_idx]['transform_matrix'])
    center_inv = np.linalg.inv(center_matrix)
    
    print(f"Using frame {center_idx} as reference (center frame)")
    print(f"Center pose:")
    print(f"  Translation: [{center_matrix[0,3]:8.3f}, {center_matrix[1,3]:8.3f}, {center_matrix[2,3]:8.3f}]")
    center_euler = rotation_matrix_to_euler(center_matrix[:3, :3])
    print(f"  Rotation (deg): [{center_euler[0]:6.1f}, {center_euler[1]:6.1f}, {center_euler[2]:6.1f}] (roll, pitch, yaw)")
    print()
    
    all_relative_translations = []
    all_relative_euler_angles = []
    
    for i, frame in enumerate(frames):
        current_matrix = np.array(frame['transform_matrix'])
        
        # Calculate relative transform: center_inv @ current
        relative_matrix = center_inv @ current_matrix
        
        # Extract relative rotation and translation
        relative_rotation = relative_matrix[:3, :3]
        relative_translation = relative_matrix[:3, 3]
        relative_euler = rotation_matrix_to_euler(relative_rotation)
        
        print(f"Frame {i:3d} (relative to center):")
        print(f"  Translation: [{relative_translation[0]:8.3f}, {relative_translation[1]:8.3f}, {relative_translation[2]:8.3f}]")
        print(f"  Rotation (deg): [{relative_euler[0]:6.1f}, {relative_euler[1]:6.1f}, {relative_euler[2]:6.1f}] (roll, pitch, yaw)")
        
        all_relative_translations.append(relative_translation)
        all_relative_euler_angles.append(relative_euler)
    
    # Calculate statistics
    all_relative_translations = np.array(all_relative_translations)
    all_relative_euler_angles = np.array(all_relative_euler_angles)
    
    print("\n" + "="*70)
    print("RELATIVE MOTION ANALYSIS (relative to center frame):")
    print("="*70)
    
    print("Translation ranges:")
    print(f"  X: [{all_relative_translations[:, 0].min():8.3f}, {all_relative_translations[:, 0].max():8.3f}] (range: {all_relative_translations[:, 0].max() - all_relative_translations[:, 0].min():6.3f})")
    print(f"  Y: [{all_relative_translations[:, 1].min():8.3f}, {all_relative_translations[:, 1].max():8.3f}] (range: {all_relative_translations[:, 1].max() - all_relative_translations[:, 1].min():6.3f})")
    print(f"  Z: [{all_relative_translations[:, 2].min():8.3f}, {all_relative_translations[:, 2].max():8.3f}] (range: {all_relative_translations[:, 2].max() - all_relative_translations[:, 2].min():6.3f})")
    
    print("\nRotation ranges (degrees):")
    print(f"  Roll:  [{all_relative_euler_angles[:, 0].min():6.1f}, {all_relative_euler_angles[:, 0].max():6.1f}] (range: {all_relative_euler_angles[:, 0].max() - all_relative_euler_angles[:, 0].min():5.1f})")
    print(f"  Pitch: [{all_relative_euler_angles[:, 1].min():6.1f}, {all_relative_euler_angles[:, 1].max():6.1f}] (range: {all_relative_euler_angles[:, 1].max() - all_relative_euler_angles[:, 1].min():5.1f})")
    print(f"  Yaw:   [{all_relative_euler_angles[:, 2].min():6.1f}, {all_relative_euler_angles[:, 2].max():6.1f}] (range: {all_relative_euler_angles[:, 2].max() - all_relative_euler_angles[:, 2].min():5.1f})")
    
    # Calculate total motion
    max_translation = np.sqrt(np.sum(all_relative_translations**2, axis=1)).max()
    max_rotation = np.sqrt(np.sum(all_relative_euler_angles**2, axis=1)).max()
    
    print(f"\nTOTAL CAMERA MOTION:")
    print(f"  Maximum translation distance from center: {max_translation:.3f} units")
    print(f"  Maximum rotation angle from center: {max_rotation:.1f} degrees")
    
    # Motion type analysis
    translation_std = np.std(all_relative_translations, axis=0)
    rotation_std = np.std(all_relative_euler_angles, axis=0)
    
    print(f"\nMOTION PATTERN ANALYSIS:")
    print(f"  Primary translation axis: {'X' if translation_std[0] == translation_std.max() else 'Y' if translation_std[1] == translation_std.max() else 'Z'} (std: {translation_std.max():.3f})")
    print(f"  Primary rotation axis: {'Roll' if rotation_std[0] == rotation_std.max() else 'Pitch' if rotation_std[1] == rotation_std.max() else 'Yaw'} (std: {rotation_std.max():.1f}°)")
    
    print(f"\nCamera intrinsics:")
    print(f"  FOV X: {poses_data.get('camera_angle_x', 'N/A')} radians")
    print(f"  Focal length X: {poses_data.get('fl_x', 'N/A')}")
    print(f"  Focal length Y: {poses_data.get('fl_y', 'N/A')}")
    print(f"  Resolution: {poses_data.get('w', 'N/A')} x {poses_data.get('h', 'N/A')}")

def main():
    if len(sys.argv) != 2:
        print("Usage: python analyze_poses.py <transforms.json>")
        print("Example: python analyze_poses.py Saved/MovieRenders/render_data/fix_line/scene_001_Target_001_height_030_GT/pose/transforms.json")
        sys.exit(1)
    
    transforms_file = sys.argv[1]
    
    if not os.path.exists(transforms_file):
        print(f"File not found: {transforms_file}")
        sys.exit(1)
    
    # Load transforms
    with open(transforms_file, 'r') as f:
        poses_data = json.load(f)
    
    print(f"Analyzing poses from: {transforms_file}")
    print(f"File: {os.path.basename(transforms_file)}")
    
    analyze_pose_sequence(poses_data)

if __name__ == "__main__":
    main()