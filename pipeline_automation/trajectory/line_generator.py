"""
Line trajectory generator for the Pipeline Automation System.

This module generates line-pattern trajectories based on the existing
generate_train_line and generate_test_line functions from the MatrixCityPlugin.
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


class LineTrajectoryGenerator(BaseTrajectoryGenerator):
    """
    Generates line-pattern trajectories for linear path coverage
    
    This generator creates camera trajectories that follow a linear path
    between two points, supporting both training and testing modes.
    """
    
    def __init__(self):
        super().__init__("line_trajectory")
    
    def generate(self, parameters: Dict[str, Any], **kwargs) -> TrajectoryDefinition:
        """
        Generate a line trajectory
        
        Args:
            parameters: Dictionary containing:
                - point1: Start point coordinates [x, y]
                - point2: End point coordinates [x, y]
                - z: Height/altitude for the trajectory
                - yaw: Base yaw rotation (optional, default: 0)
                - mode: "train" or "test" (optional, default: "train")
                - dense: Dense sampling for train mode (optional, default: False)
                - fps: Frames per second (optional, default: 24)
                - fov: Field of view (optional, default: 90)
            
        Returns:
            TrajectoryDefinition object
        """
        if not self.validate_parameters(parameters):
            raise ValueError("Invalid parameters for line trajectory generation")
        
        point1 = parameters['point1']
        point2 = parameters['point2']
        z = parameters['z']
        yaw = parameters.get('yaw', 0.0)
        mode = parameters.get('mode', 'train')
        fps = parameters.get('fps', self.default_fps)
        fov = parameters.get('fov', self.default_fov)
        
        # Generate keyframes based on mode
        if mode == 'train':
            keyframes = self._generate_train_line(point1, point2, z, yaw, fps, fov, parameters)
        elif mode == 'test':
            keyframes = self._generate_test_line(point1, point2, z, yaw, fps, fov, parameters)
        else:
            raise ValueError(f"Unknown mode: {mode}. Must be 'train' or 'test'")
        
        # Calculate duration
        duration = self.calculate_duration(keyframes)
        
        return TrajectoryDefinition(
            name=f"{self.name}_{mode}",
            keyframes=keyframes,
            duration=duration,
            trajectory_type="line"
        )
    
    def _generate_train_line(self, point1: List[float], point2: List[float], z: float,
                            yaw: float, fps: float, fov: float, parameters: Dict[str, Any]) -> List[CameraKeyframe]:
        """
        Generate training line trajectory (systematic rotations)
        
        Based on the original generate_train_line function
        """
        # Calculate distance and sampling
        distance = self.calculate_distance(point1, point2)
        dense = parameters.get('dense', False)
        
        if dense:
            instance = int(distance / 100) + 1  # Dense sampling
        else:
            instance = int(distance / 500) + 1  # Sparse sampling
        
        keyframes = []
        current_time = 0.0
        time_step = 1.0 / fps
        
        # Generate keyframes for different rotations
        rotations = [
            (0, 0, yaw),           # Base rotation
            (0, 0, 90 + yaw),      # 90° rotation
            (0, 0, 180 + yaw),     # 180° rotation
            (0, 0, 270 + yaw),     # 270° rotation
            (0, 90, yaw)           # Looking up
        ]
        
        for roll, pitch, yaw_angle in rotations:
            # Start position
            keyframes.append(self.create_keyframe(
                time=current_time,
                position=(point1[0], point1[1], z),
                rotation=(roll, pitch, yaw_angle),
                fov=fov
            ))
            current_time += instance * time_step
            
            # End position
            keyframes.append(self.create_keyframe(
                time=current_time,
                position=(point2[0], point2[1], z),
                rotation=(roll, pitch, yaw_angle),
                fov=fov
            ))
            current_time += time_step
        
        return keyframes
    
    def _generate_test_line(self, point1: List[float], point2: List[float], z: float,
                           yaw: float, fps: float, fov: float, parameters: Dict[str, Any]) -> List[CameraKeyframe]:
        """
        Generate test line trajectory (randomized sampling)
        
        Based on the original generate_test_line function
        """
        # Modify points for test mode (add offset)
        test_point1 = point1.copy()
        test_point2 = point2.copy()
        
        offset_distance = 570  # Offset distance from original function
        test_point1[0] += math.cos(math.radians(yaw)) * offset_distance
        test_point1[1] += math.sin(math.radians(yaw)) * offset_distance
        test_point2[0] -= math.cos(math.radians(yaw)) * offset_distance
        test_point2[1] -= math.sin(math.radians(yaw)) * offset_distance
        
        # Random yaw for test mode
        random_yaw = np.random.randint(0, 90)
        
        # Calculate distance and sampling
        distance = self.calculate_distance(test_point1, test_point2)
        instance = int(distance / 4830) + 1  # Test interval
        
        keyframes = []
        current_time = 0.0
        time_step = 1.0 / fps
        
        # Generate keyframes for different rotations with random yaw
        rotations = [
            (0, 0, random_yaw),           # Random base rotation
            (0, 0, 90 + random_yaw),      # 90° + random
            (0, 0, 180 + random_yaw),     # 180° + random
            (0, 0, 270 + random_yaw),     # 270° + random
            (0, 90, random_yaw)           # Looking up + random
        ]
        
        for roll, pitch, yaw_angle in rotations:
            # Start position
            keyframes.append(self.create_keyframe(
                time=current_time,
                position=(test_point1[0], test_point1[1], z),
                rotation=(roll, pitch, yaw_angle),
                fov=fov
            ))
            current_time += instance * time_step
            
            # End position
            keyframes.append(self.create_keyframe(
                time=current_time,
                position=(test_point2[0], test_point2[1], z),
                rotation=(roll, pitch, yaw_angle),
                fov=fov
            ))
            current_time += time_step
        
        return keyframes
    
    def validate_parameters(self, parameters: Dict[str, Any]) -> bool:
        """
        Validate parameters for line trajectory generation
        
        Args:
            parameters: Parameters to validate
            
        Returns:
            True if valid, False otherwise
        """
        if not self.validate_common_parameters(parameters):
            return False
        
        required_params = ['point1', 'point2', 'z']
        for param in required_params:
            if param not in parameters:
                return False
        
        # Validate point formats
        point1 = parameters['point1']
        point2 = parameters['point2']
        
        if not (isinstance(point1, list) and len(point1) == 2):
            return False
        
        if not (isinstance(point2, list) and len(point2) == 2):
            return False
        
        # Validate numeric values
        try:
            for val in point1 + point2 + [parameters['z']]:
                float(val)
        except (ValueError, TypeError):
            return False
        
        # Validate yaw if provided
        if 'yaw' in parameters:
            try:
                float(parameters['yaw'])
            except (ValueError, TypeError):
                return False
        
        # Validate mode if provided
        if 'mode' in parameters:
            if parameters['mode'] not in ['train', 'test']:
                return False
        
        return True
    
    def get_required_parameters(self) -> List[str]:
        """Get required parameters for line trajectory generation"""
        return ['point1', 'point2', 'z']
    
    def get_optional_parameters(self) -> Dict[str, Any]:
        """Get optional parameters with defaults"""
        return {
            'yaw': 0.0,
            'mode': 'train',
            'dense': False,
            'fps': 24.0,
            'fov': 90.0
        }