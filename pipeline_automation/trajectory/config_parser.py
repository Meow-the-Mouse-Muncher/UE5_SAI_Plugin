"""
Trajectory configuration file parser for the Pipeline Automation System.

This module handles parsing and validation of trajectory configuration files
in JSON and YAML formats, converting them to TrajectoryDefinition objects.
"""

import json
import yaml
from pathlib import Path
from typing import Dict, Any, List, Optional

try:
    from ..models import TrajectoryDefinition, TrajectoryConfig, CameraKeyframe
    from .trajectory_factory import get_trajectory_factory
except ImportError:
    import sys
    sys.path.append(str(Path(__file__).parent.parent))
    from models import TrajectoryDefinition, TrajectoryConfig, CameraKeyframe
    from trajectory_factory import get_trajectory_factory


class TrajectoryConfigParser:
    """
    Parser for trajectory configuration files
    
    Supports parsing trajectory definitions from JSON and YAML files,
    with validation and conversion to TrajectoryDefinition objects.
    """
    
    def __init__(self):
        """Initialize the trajectory config parser"""
        self.trajectory_factory = get_trajectory_factory()
    
    def parse_config_file(self, config_path: Path) -> TrajectoryDefinition:
        """
        Parse trajectory configuration from file
        
        Args:
            config_path: Path to configuration file (.json or .yaml/.yml)
            
        Returns:
            TrajectoryDefinition object
            
        Raises:
            FileNotFoundError: If config file doesn't exist
            ValueError: If config format is invalid
        """
        config_path = Path(config_path)
        
        if not config_path.exists():
            raise FileNotFoundError(f"Configuration file not found: {config_path}")
        
        # Load configuration data
        try:
            if config_path.suffix.lower() in ['.yaml', '.yml']:
                with open(config_path, 'r') as f:
                    config_data = yaml.safe_load(f)
            else:
                with open(config_path, 'r') as f:
                    config_data = json.load(f)
        except Exception as e:
            raise ValueError(f"Failed to parse configuration file: {e}")
        
        return self.parse_config_data(config_data, config_path.stem)
    
    def parse_config_data(self, config_data: Dict[str, Any], name: str = "trajectory") -> TrajectoryDefinition:
        """
        Parse trajectory configuration from data dictionary
        
        Args:
            config_data: Configuration data dictionary
            name: Default name for the trajectory
            
        Returns:
            TrajectoryDefinition object
        """
        # Validate required fields
        if 'trajectory_type' not in config_data:
            raise ValueError("Missing required field: trajectory_type")
        
        trajectory_type = config_data['trajectory_type']
        
        # Handle different configuration formats
        if 'keyframes' in config_data:
            # Direct keyframe definition
            return self._parse_keyframe_config(config_data, name)
        elif 'parameters' in config_data:
            # Generator-based definition
            return self._parse_generator_config(config_data, name)
        else:
            # Inline parameters
            return self._parse_inline_config(config_data, name)
    
    def _parse_keyframe_config(self, config_data: Dict[str, Any], name: str) -> TrajectoryDefinition:
        """Parse configuration with explicit keyframes"""
        keyframes = []
        
        for kf_data in config_data['keyframes']:
            keyframe = CameraKeyframe(
                time=kf_data['time'],
                position=tuple(kf_data['position']),
                rotation=tuple(kf_data['rotation']),
                fov=kf_data.get('fov', 90.0)
            )
            keyframes.append(keyframe)
        
        # Calculate duration if not provided
        duration = config_data.get('duration')
        if duration is None and keyframes:
            duration = max(kf.time for kf in keyframes)
        
        return TrajectoryDefinition(
            name=config_data.get('name', name),
            keyframes=keyframes,
            duration=duration or 0.0,
            trajectory_type=config_data.get('trajectory_type', 'custom')
        )
    
    def _parse_generator_config(self, config_data: Dict[str, Any], name: str) -> TrajectoryDefinition:
        """Parse configuration using trajectory generators"""
        trajectory_type = config_data['trajectory_type']
        parameters = config_data['parameters']
        
        # Add name to parameters if not present
        if 'name' not in parameters:
            parameters = parameters.copy()
            parameters['name'] = config_data.get('name', name)
        
        return self.trajectory_factory.generate_trajectory(trajectory_type, parameters)
    
    def _parse_inline_config(self, config_data: Dict[str, Any], name: str) -> TrajectoryDefinition:
        """Parse configuration with inline parameters"""
        trajectory_type = config_data['trajectory_type']
        
        # Extract parameters (everything except trajectory_type and name)
        parameters = {k: v for k, v in config_data.items() 
                     if k not in ['trajectory_type', 'name']}
        
        # Add name if provided
        if 'name' in config_data:
            parameters['name'] = config_data['name']
        else:
            parameters['name'] = name
        
        return self.trajectory_factory.generate_trajectory(trajectory_type, parameters)
    
    def validate_config_file(self, config_path: Path) -> List[str]:
        """
        Validate trajectory configuration file
        
        Args:
            config_path: Path to configuration file
            
        Returns:
            List of validation error messages (empty if valid)
        """
        errors = []
        
        try:
            trajectory = self.parse_config_file(config_path)
            
            # Basic validation
            if not trajectory.keyframes:
                errors.append("Trajectory has no keyframes")
            
            if trajectory.duration <= 0:
                errors.append("Trajectory duration must be positive")
            
            # Validate keyframe sequence
            for i, keyframe in enumerate(trajectory.keyframes):
                if keyframe.time < 0:
                    errors.append(f"Keyframe {i} has negative time: {keyframe.time}")
                
                if i > 0 and keyframe.time < trajectory.keyframes[i-1].time:
                    errors.append(f"Keyframe {i} time is not in ascending order")
        
        except Exception as e:
            errors.append(f"Configuration parsing failed: {e}")
        
        return errors
    
    def create_sample_configs(self, output_dir: Path) -> None:
        """
        Create sample trajectory configuration files
        
        Args:
            output_dir: Directory to create sample files in
        """
        output_dir = Path(output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)
        
        # Box trajectory sample
        box_config = {
            "name": "sample_box_trajectory",
            "trajectory_type": "box",
            "parameters": {
                "line1": [0, 0, 10000, 0],
                "line2": [0, 5000, 10000, 5000],
                "z": 1000,
                "mode": "train",
                "interval": 800,
                "fps": 24,
                "fov": 90
            }
        }
        
        with open(output_dir / "box_trajectory.json", 'w') as f:
            json.dump(box_config, f, indent=2)
        
        # Line trajectory sample
        line_config = {
            "name": "sample_line_trajectory",
            "trajectory_type": "line",
            "parameters": {
                "point1": [0, 0],
                "point2": [5000, 5000],
                "z": 800,
                "yaw": 45,
                "mode": "train",
                "dense": False,
                "fps": 24,
                "fov": 90
            }
        }
        
        with open(output_dir / "line_trajectory.json", 'w') as f:
            json.dump(line_config, f, indent=2)
        
        # Custom keyframe trajectory sample
        custom_config = {
            "name": "sample_custom_trajectory",
            "trajectory_type": "custom",
            "duration": 10.0,
            "keyframes": [
                {
                    "time": 0.0,
                    "position": [0, 0, 1000],
                    "rotation": [0, -45, 0],
                    "fov": 90
                },
                {
                    "time": 5.0,
                    "position": [5000, 0, 1000],
                    "rotation": [0, -45, 90],
                    "fov": 90
                },
                {
                    "time": 10.0,
                    "position": [5000, 5000, 1000],
                    "rotation": [0, -45, 180],
                    "fov": 90
                }
            ]
        }
        
        with open(output_dir / "custom_trajectory.json", 'w') as f:
            json.dump(custom_config, f, indent=2)
        
        # YAML format sample
        yaml_config = {
            "name": "sample_yaml_trajectory",
            "trajectory_type": "box",
            "line1": [0, 0, 8000, 0],
            "line2": [0, 4000, 8000, 4000],
            "z": 1200,
            "mode": "train",
            "interval": 1000
        }
        
        with open(output_dir / "yaml_trajectory.yaml", 'w') as f:
            yaml.dump(yaml_config, f, default_flow_style=False, indent=2)


# Convenience functions
def parse_trajectory_config(config_path: Path) -> TrajectoryDefinition:
    """
    Convenience function to parse trajectory configuration
    
    Args:
        config_path: Path to configuration file
        
    Returns:
        TrajectoryDefinition object
    """
    parser = TrajectoryConfigParser()
    return parser.parse_config_file(config_path)


def validate_trajectory_config(config_path: Path) -> List[str]:
    """
    Convenience function to validate trajectory configuration
    
    Args:
        config_path: Path to configuration file
        
    Returns:
        List of validation errors (empty if valid)
    """
    parser = TrajectoryConfigParser()
    return parser.validate_config_file(config_path)


def create_trajectory_samples(output_dir: Path) -> None:
    """
    Convenience function to create sample trajectory configurations
    
    Args:
        output_dir: Directory to create samples in
    """
    parser = TrajectoryConfigParser()
    parser.create_sample_configs(output_dir)