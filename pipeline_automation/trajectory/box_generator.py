"""
Box trajectory generator for the Pipeline Automation System.

This module generates box-pattern trajectories based on the existing
generate_train_box and generate_test_box functions from the MatrixCityPlugin.
"""

import math
import numpy as np
from typing import List, Dict, Any, Tuple, Optional

try:
    from ..models import TrajectoryDefinition, CameraKeyframe
    from .base_generator import BaseTrajectoryGenerator
except ImportError:
    import sys
    from pathlib import Path
    sys.path.append(str(Path(__file__).parent.parent))
    from models import TrajectoryDefinition, CameraKeyframe
    from base_generator import BaseTrajectoryGenerator


class BoxTrajectoryGenerator(BaseTrajectoryGenerator):
    """
    Generates box-pattern trajectories for systematic area coverage
    
    This generator creates camera trajectories that cover a rectangular area
    in a systematic pattern, supporting both training and testing modes.
    """
    
    def __init__(self):
        super().__init__("box_trajectory")
    
    def generate(self, parameters: Dict[str, Any], **kwargs) -> TrajectoryDefinition:
        """
        Generate a box trajectory
        
        Args:
            parameters: Dictionary containing:
                - line1: First line coordinates [x1, y1, x2, y2]
                - line2: Second line coordinates [x1, y1, x2, y2]
                - z: Height/altitude for the trajectory
                - mode: "train" or "test" (optional, default: "train")
                - interval: Sampling interval (optional)
                - fps: Frames per second (optional, default: 24)
                - fov: Field of view (optional, default: 90)
            
        Returns:
            TrajectoryDefinition object
        """
        if not self.validate_parameters(parameters):
            raise ValueError("Invalid parameters for box trajectory generation")
        
        line1 = parameters['line1']
        line2 = parameters['line2']
        z = parameters['z']
        mode = parameters.get('mode', 'train')
        fps = parameters.get('fps', self.default_fps)
        fov = parameters.get('fov', self.default_fov)
        
        # Generate keyframes based on mode
        if mode == 'train':
            keyframes = self._generate_train_box(line1, line2, z, fps, fov, parameters)
        elif mode == 'test':
            keyframes = self._generate_test_box(line1, line2, z, fps, fov, parameters)
        else:
            raise ValueError(f"Unknown mode: {mode}. Must be 'train' or 'test'")
        
        # Calculate duration
        duration = self.calculate_duration(keyframes)
        
        return TrajectoryDefinition(
            name=f"{self.name}_{mode}",
            keyframes=keyframes,
            duration=duration,
            trajectory_type="box"
        )
    
    def _generate_train_box(self, line1: List[float], line2: List[float], z: float, 
                           fps: float, fov: float, parameters: Dict[str, Any]) -> List[CameraKeyframe]:
        """
        Generate training box trajectory (systematic coverage)
        
        Based on the original generate_train_box function
        """
        x11, y11, x12, y12 = line1
        x21, y21, x22, y22 = line2
        
        # Validate box constraints
        assert y11 == y12, "First line must be horizontal"
        assert y21 == y22, "Second line must be horizontal"
        assert x11 == x21, "Lines must have same start x-coordinate"
        assert x12 == x22, "Lines must have same end x-coordinate"
        
        # Calculate dimensions
        w = self.calculate_distance([x11, y11], [x12, y12])
        h = self.calculate_distance([x11, y11], [x21, y21])
        
        # Get interval (sampling distance)
        interval = parameters.get('interval', 800.0)  # Default train interval
        
        # Calculate sampling points
        w_instance = int(w / interval) + 2
        h_instance = int(h / interval) + 1
        
        # Generate sampling positions
        x_start = self.interpolate_linear(x11, x12, w_instance)
        y_start = self.interpolate_linear(y11, y12, w_instance)
        x_end = self.interpolate_linear(x21, x22, w_instance)
        y_end = self.interpolate_linear(y21, y22, w_instance)
        
        keyframes = []
        current_time = 0.0
        time_step = 1.0 / fps
        
        # Determine pitch based on height
        pitch = -60 if z > 25000 else -45
        
        # Generate keyframes for each rotation (0°, 90°, 180°, 270°)
        for yaw_offset in [0, 90, 180, 270]:
            for i in range(w_instance):
                # Start position
                keyframes.append(self.create_keyframe(
                    time=current_time,
                    position=(x_start[i], y_start[i], z),
                    rotation=(0, pitch, yaw_offset),
                    fov=fov
                ))
                current_time += h_instance * time_step
                
                # End position
                keyframes.append(self.create_keyframe(
                    time=current_time,
                    position=(x_end[i], y_end[i], z),
                    rotation=(0, pitch, yaw_offset),
                    fov=fov
                ))
                current_time += time_step
        
        return keyframes
    
    def _generate_test_box(self, line1: List[float], line2: List[float], z: float,
                          fps: float, fov: float, parameters: Dict[str, Any]) -> List[CameraKeyframe]:
        """
        Generate test box trajectory (randomized sampling)
        
        Based on the original generate_test_box function
        """
        x11, y11, x12, y12 = line1
        x21, y21, x22, y22 = line2
        
        # Validate box constraints
        assert y11 == y12, "First line must be horizontal"
        assert y21 == y22, "Second line must be horizontal"
        assert x11 == x21, "Lines must have same start x-coordinate"
        assert x12 == x22, "Lines must have same end x-coordinate"
        
        # Calculate dimensions
        w = self.calculate_distance([x11, y11], [x12, y12])
        h = self.calculate_distance([x11, y11], [x21, y21])
        
        # Get interval (larger for test mode)
        interval = parameters.get('interval', 4501.0)  # Default test interval
        
        # Calculate sampling points
        w_instance = int(w / interval) + 2
        h_instance = int(h / interval) + 1
        
        # Generate sampling positions
        x_start = self.interpolate_linear(x11, x12, w_instance)
        y_start = self.interpolate_linear(y11, y12, w_instance)
        x_end = self.interpolate_linear(x21, x22, w_instance)
        y_end = self.interpolate_linear(y21, y22, w_instance)
        
        keyframes = []
        current_time = 0.0
        time_step = 1.0 / fps
        
        # Generate keyframes with random orientations
        for i in range(w_instance):
            # Random pitch and yaw for test mode
            pitch = np.random.randint(-60, -44)
            yaw = np.random.randint(0, 361)
            
            # Start position
            keyframes.append(self.create_keyframe(
                time=current_time,
                position=(x_start[i], y_start[i], z),
                rotation=(0, pitch, yaw),
                fov=fov
            ))
            current_time += h_instance * time_step
            
            # End position (same orientation)
            keyframes.append(self.create_keyframe(
                time=current_time,
                position=(x_end[i], y_end[i], z),
                rotation=(0, pitch, yaw),
                fov=fov
            ))
            current_time += time_step
        
        return keyframes
    
    def validate_parameters(self, parameters: Dict[str, Any]) -> bool:
        """
        Validate parameters for box trajectory generation
        
        Args:
            parameters: Parameters to validate
            
        Returns:
            True if valid, False otherwise
        """
        if not self.validate_common_parameters(parameters):
            return False
        
        required_params = ['line1', 'line2', 'z']
        for param in required_params:
            if param not in parameters:
                return False
        
        # Validate line formats
        line1 = parameters['line1']
        line2 = parameters['line2']
        
        if not (isinstance(line1, list) and len(line1) == 4):
            return False
        
        if not (isinstance(line2, list) and len(line2) == 4):
            return False
        
        # Validate numeric values
        try:
            for val in line1 + line2 + [parameters['z']]:
                float(val)
        except (ValueError, TypeError):
            return False
        
        # Validate mode if provided
        if 'mode' in parameters:
            if parameters['mode'] not in ['train', 'test']:
                return False
        
        return True
    
    def get_required_parameters(self) -> List[str]:
        """Get required parameters for box trajectory generation"""
        return ['line1', 'line2', 'z']
    
    def get_optional_parameters(self) -> Dict[str, Any]:
        """Get optional parameters with defaults"""
        return {
            'mode': 'train',
            'interval': 800.0,
            'fps': 24.0,
            'fov': 90.0
        }