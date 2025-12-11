"""
Base trajectory generator class for the Pipeline Automation System.

This module provides the base interface and common functionality for all
trajectory generators.
"""

import math
from abc import ABC, abstractmethod
from typing import List, Dict, Any, Optional, Tuple
from pathlib import Path

try:
    from ..models import TrajectoryDefinition, CameraKeyframe
except ImportError:
    import sys
    sys.path.append(str(Path(__file__).parent.parent))
    from models import TrajectoryDefinition, CameraKeyframe


class BaseTrajectoryGenerator(ABC):
    """
    Base class for all trajectory generators
    
    This class defines the interface that all trajectory generators must implement
    and provides common utility functions for trajectory generation.
    """
    
    def __init__(self, name: str):
        """
        Initialize base trajectory generator
        
        Args:
            name: Name of the trajectory generator
        """
        self.name = name
        self.default_fps = 24.0
        self.default_fov = 90.0
    
    @abstractmethod
    def generate(self, parameters: Dict[str, Any], **kwargs) -> TrajectoryDefinition:
        """
        Generate a trajectory based on the provided parameters
        
        Args:
            parameters: Dictionary of generation parameters
            **kwargs: Additional keyword arguments
            
        Returns:
            TrajectoryDefinition object containing the generated trajectory
        """
        pass
    
    @abstractmethod
    def validate_parameters(self, parameters: Dict[str, Any]) -> bool:
        """
        Validate the parameters for trajectory generation
        
        Args:
            parameters: Dictionary of parameters to validate
            
        Returns:
            True if parameters are valid, False otherwise
        """
        pass
    
    def get_required_parameters(self) -> List[str]:
        """
        Get list of required parameters for this generator
        
        Returns:
            List of required parameter names
        """
        return []
    
    def get_optional_parameters(self) -> Dict[str, Any]:
        """
        Get dictionary of optional parameters with their default values
        
        Returns:
            Dictionary of optional parameters and defaults
        """
        return {}
    
    def calculate_duration(self, keyframes: List[CameraKeyframe]) -> float:
        """
        Calculate trajectory duration from keyframes
        
        Args:
            keyframes: List of camera keyframes
            
        Returns:
            Duration in seconds
        """
        if not keyframes:
            return 0.0
        
        return max(kf.time for kf in keyframes)
    
    def calculate_distance(self, point1: Tuple[float, float], point2: Tuple[float, float]) -> float:
        """
        Calculate distance between two 2D points
        
        Args:
            point1: First point (x, y)
            point2: Second point (x, y)
            
        Returns:
            Distance between points
        """
        return math.sqrt((point2[0] - point1[0])**2 + (point2[1] - point1[1])**2)
    
    def interpolate_linear(self, start: float, end: float, steps: int) -> List[float]:
        """
        Generate linear interpolation between start and end values
        
        Args:
            start: Starting value
            end: Ending value
            steps: Number of steps
            
        Returns:
            List of interpolated values
        """
        if steps <= 1:
            return [start]
        
        step_size = (end - start) / (steps - 1)
        return [start + i * step_size for i in range(steps)]
    
    def create_keyframe(self, time: float, position: Tuple[float, float, float], 
                       rotation: Tuple[float, float, float], fov: Optional[float] = None) -> CameraKeyframe:
        """
        Create a camera keyframe
        
        Args:
            time: Time of the keyframe
            position: Camera position (x, y, z)
            rotation: Camera rotation (roll, pitch, yaw)
            fov: Field of view (optional)
            
        Returns:
            CameraKeyframe object
        """
        return CameraKeyframe(
            time=time,
            position=position,
            rotation=rotation,
            fov=fov or self.default_fov
        )
    
    def apply_height_offset(self, trajectory: TrajectoryDefinition, height_offset: float) -> TrajectoryDefinition:
        """
        Apply height offset to an existing trajectory
        
        Args:
            trajectory: Original trajectory
            height_offset: Height offset to apply
            
        Returns:
            New trajectory with height offset applied
        """
        return trajectory.apply_height_offset(height_offset)
    
    def validate_common_parameters(self, parameters: Dict[str, Any]) -> bool:
        """
        Validate common parameters that all generators might use
        
        Args:
            parameters: Parameters to validate
            
        Returns:
            True if valid, False otherwise
        """
        # Check for negative values where they don't make sense
        if 'fps' in parameters and parameters['fps'] <= 0:
            return False
        
        if 'fov' in parameters and (parameters['fov'] <= 0 or parameters['fov'] >= 180):
            return False
        
        if 'interval' in parameters and parameters['interval'] <= 0:
            return False
        
        return True
    
    def get_generator_info(self) -> Dict[str, Any]:
        """
        Get information about this generator
        
        Returns:
            Dictionary with generator information
        """
        return {
            "name": self.name,
            "type": self.__class__.__name__,
            "required_parameters": self.get_required_parameters(),
            "optional_parameters": self.get_optional_parameters()
        }