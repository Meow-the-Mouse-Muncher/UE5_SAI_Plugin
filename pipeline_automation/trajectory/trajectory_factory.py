"""
Trajectory factory for creating trajectory generators.

This module provides a factory pattern for creating different types of
trajectory generators and managing trajectory generation workflows.
"""

from typing import Dict, Any, Optional, List, Type
from pathlib import Path

try:
    from ..models import TrajectoryDefinition, TrajectoryConfig
    from .base_generator import BaseTrajectoryGenerator
    from .box_generator import BoxTrajectoryGenerator
    from .line_generator import LineTrajectoryGenerator
except ImportError:
    import sys
    sys.path.append(str(Path(__file__).parent.parent))
    from models import TrajectoryDefinition, TrajectoryConfig
    from base_generator import BaseTrajectoryGenerator
    from box_generator import BoxTrajectoryGenerator
    from line_generator import LineTrajectoryGenerator


class TrajectoryFactory:
    """
    Factory class for creating and managing trajectory generators
    
    This factory provides a centralized way to create trajectory generators
    and generate trajectories based on configuration parameters.
    """
    
    def __init__(self):
        """Initialize the trajectory factory"""
        self._generators: Dict[str, Type[BaseTrajectoryGenerator]] = {}
        self._register_default_generators()
    
    def _register_default_generators(self) -> None:
        """Register the default trajectory generators"""
        self.register_generator("box", BoxTrajectoryGenerator)
        self.register_generator("line", LineTrajectoryGenerator)
    
    def register_generator(self, trajectory_type: str, generator_class: Type[BaseTrajectoryGenerator]) -> None:
        """
        Register a trajectory generator class
        
        Args:
            trajectory_type: Type identifier for the generator
            generator_class: Generator class to register
        """
        self._generators[trajectory_type] = generator_class
    
    def get_available_types(self) -> List[str]:
        """
        Get list of available trajectory types
        
        Returns:
            List of registered trajectory type names
        """
        return list(self._generators.keys())
    
    def create_generator(self, trajectory_type: str) -> BaseTrajectoryGenerator:
        """
        Create a trajectory generator instance
        
        Args:
            trajectory_type: Type of trajectory generator to create
            
        Returns:
            Trajectory generator instance
            
        Raises:
            ValueError: If trajectory type is not registered
        """
        if trajectory_type not in self._generators:
            available_types = ", ".join(self.get_available_types())
            raise ValueError(f"Unknown trajectory type: {trajectory_type}. Available types: {available_types}")
        
        generator_class = self._generators[trajectory_type]
        return generator_class()
    
    def generate_trajectory(self, trajectory_type: str, parameters: Dict[str, Any], **kwargs) -> TrajectoryDefinition:
        """
        Generate a trajectory using the specified type and parameters
        
        Args:
            trajectory_type: Type of trajectory to generate
            parameters: Parameters for trajectory generation
            **kwargs: Additional keyword arguments
            
        Returns:
            Generated TrajectoryDefinition
            
        Raises:
            ValueError: If trajectory type is unknown or parameters are invalid
        """
        generator = self.create_generator(trajectory_type)
        return generator.generate(parameters, **kwargs)
    
    def generate_from_config(self, config: TrajectoryConfig) -> TrajectoryDefinition:
        """
        Generate a trajectory from a TrajectoryConfig object
        
        Args:
            config: TrajectoryConfig containing generation parameters
            
        Returns:
            Generated TrajectoryDefinition
        """
        # Validate config first
        config.validate()
        
        return self.generate_trajectory(config.trajectory_type, config.parameters)
    
    def generate_with_height_variants(self, trajectory_type: str, parameters: Dict[str, Any], 
                                    height_variants: List[float]) -> List[TrajectoryDefinition]:
        """
        Generate multiple trajectories with different height offsets
        
        Args:
            trajectory_type: Type of trajectory to generate
            parameters: Base parameters for trajectory generation
            height_variants: List of height offsets to apply
            
        Returns:
            List of TrajectoryDefinition objects with height variants
        """
        base_trajectory = self.generate_trajectory(trajectory_type, parameters)
        trajectories = []
        
        for height_offset in height_variants:
            if height_offset == 0.0:
                # Use original trajectory for zero offset
                trajectories.append(base_trajectory)
            else:
                # Apply height offset
                variant_trajectory = base_trajectory.apply_height_offset(height_offset)
                trajectories.append(variant_trajectory)
        
        return trajectories
    
    def validate_parameters(self, trajectory_type: str, parameters: Dict[str, Any]) -> bool:
        """
        Validate parameters for a specific trajectory type
        
        Args:
            trajectory_type: Type of trajectory
            parameters: Parameters to validate
            
        Returns:
            True if parameters are valid, False otherwise
        """
        try:
            generator = self.create_generator(trajectory_type)
            return generator.validate_parameters(parameters)
        except ValueError:
            return False
    
    def get_generator_info(self, trajectory_type: str) -> Dict[str, Any]:
        """
        Get information about a specific generator
        
        Args:
            trajectory_type: Type of trajectory generator
            
        Returns:
            Dictionary with generator information
            
        Raises:
            ValueError: If trajectory type is unknown
        """
        generator = self.create_generator(trajectory_type)
        return generator.get_generator_info()
    
    def get_all_generator_info(self) -> Dict[str, Dict[str, Any]]:
        """
        Get information about all registered generators
        
        Returns:
            Dictionary mapping trajectory types to their information
        """
        info = {}
        for trajectory_type in self.get_available_types():
            info[trajectory_type] = self.get_generator_info(trajectory_type)
        return info


# Global factory instance
_trajectory_factory = TrajectoryFactory()


def get_trajectory_factory() -> TrajectoryFactory:
    """
    Get the global trajectory factory instance
    
    Returns:
        Global TrajectoryFactory instance
    """
    return _trajectory_factory


def create_trajectory(trajectory_type: str, parameters: Dict[str, Any], **kwargs) -> TrajectoryDefinition:
    """
    Convenience function to create a trajectory
    
    Args:
        trajectory_type: Type of trajectory to generate
        parameters: Parameters for trajectory generation
        **kwargs: Additional keyword arguments
        
    Returns:
        Generated TrajectoryDefinition
    """
    return _trajectory_factory.generate_trajectory(trajectory_type, parameters, **kwargs)


def create_box_trajectory(line1: List[float], line2: List[float], z: float, 
                         mode: str = "train", **kwargs) -> TrajectoryDefinition:
    """
    Convenience function to create a box trajectory
    
    Args:
        line1: First line coordinates [x1, y1, x2, y2]
        line2: Second line coordinates [x1, y1, x2, y2]
        z: Height/altitude
        mode: "train" or "test"
        **kwargs: Additional parameters
        
    Returns:
        Generated box TrajectoryDefinition
    """
    parameters = {
        'line1': line1,
        'line2': line2,
        'z': z,
        'mode': mode,
        **kwargs
    }
    return create_trajectory("box", parameters)


def create_line_trajectory(point1: List[float], point2: List[float], z: float,
                          yaw: float = 0.0, mode: str = "train", **kwargs) -> TrajectoryDefinition:
    """
    Convenience function to create a line trajectory
    
    Args:
        point1: Start point [x, y]
        point2: End point [x, y]
        z: Height/altitude
        yaw: Base yaw rotation
        mode: "train" or "test"
        **kwargs: Additional parameters
        
    Returns:
        Generated line TrajectoryDefinition
    """
    parameters = {
        'point1': point1,
        'point2': point2,
        'z': z,
        'yaw': yaw,
        'mode': mode,
        **kwargs
    }
    return create_trajectory("line", parameters)