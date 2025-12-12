"""
Configuration management system for the Pipeline Automation System.

This module handles loading, parsing, and validation of configuration files
including pipeline configs, trajectory definitions, and placement configurations.
"""

import json
import yaml
from pathlib import Path
from typing import Dict, Any, Optional, List, Union
import logging

try:
    from .models import (
        PipelineConfig, TrajectoryConfig, TrajectoryDefinition, 
        CameraKeyframe, PlacementConfig, MapInfo
    )
except ImportError:
    from models import (
        PipelineConfig, TrajectoryConfig, TrajectoryDefinition, 
        CameraKeyframe, PlacementConfig, MapInfo
    )


logger = logging.getLogger(__name__)


class ConfigManager:
    """Manages all configuration loading and validation for the pipeline"""
    
    def __init__(self, config_root: Optional[Path] = None):
        """
        Initialize configuration manager
        
        Args:
            config_root: Root directory for configuration files
        """
        self.config_root = Path(config_root) if config_root else Path.cwd()
        self._placement_configs: Dict[str, PlacementConfig] = {}
        self._trajectory_cache: Dict[str, TrajectoryDefinition] = {}
    
    def load_pipeline_config(self, config_path: Path) -> PipelineConfig:
        """
        Load main pipeline configuration from JSON or YAML file
        
        Args:
            config_path: Path to configuration file
            
        Returns:
            PipelineConfig object
            
        Raises:
            FileNotFoundError: If config file doesn't exist
            ValueError: If config is invalid
        """
        config_path = Path(config_path)
        
        if not config_path.exists():
            raise FileNotFoundError(f"Configuration file not found: {config_path}")
        
        try:
            if config_path.suffix.lower() in ['.yaml', '.yml']:
                with open(config_path, 'r') as f:
                    config_data = yaml.safe_load(f)
            else:
                with open(config_path, 'r') as f:
                    config_data = json.load(f)
        except Exception as e:
            raise ValueError(f"Failed to parse configuration file: {e}")
        
        # Validate required fields
        required_fields = ['maps_directory', 'trajectory_file_path', 'output_directory']
        for field in required_fields:
            if field not in config_data:
                raise ValueError(f"Missing required field in config: {field}")
        
        # Convert relative paths to absolute paths
        if not Path(config_data['maps_directory']).is_absolute():
            config_data['maps_directory'] = self.config_root / config_data['maps_directory']
        
        if not Path(config_data['trajectory_file_path']).is_absolute():
            config_data['trajectory_file_path'] = self.config_root / config_data['trajectory_file_path']
            
        if not Path(config_data['output_directory']).is_absolute():
            config_data['output_directory'] = self.config_root / config_data['output_directory']
        
        # Set default height variants if not provided
        if 'height_variants' not in config_data:
            config_data['height_variants'] = [0.0, 500.0, 1000.0]
        
        return PipelineConfig(**config_data)
    
    def load_trajectory_definition(self, trajectory_path: Path) -> TrajectoryDefinition:
        """
        Load trajectory definition from file
        
        Args:
            trajectory_path: Path to trajectory definition file
            
        Returns:
            TrajectoryDefinition object
        """
        trajectory_path = Path(trajectory_path)
        
        # Check cache first
        cache_key = str(trajectory_path.absolute())
        if cache_key in self._trajectory_cache:
            return self._trajectory_cache[cache_key]
        
        if not trajectory_path.exists():
            raise FileNotFoundError(f"Trajectory file not found: {trajectory_path}")
        
        try:
            if trajectory_path.suffix.lower() in ['.yaml', '.yml']:
                with open(trajectory_path, 'r') as f:
                    traj_data = yaml.safe_load(f)
            else:
                with open(trajectory_path, 'r') as f:
                    traj_data = json.load(f)
        except Exception as e:
            raise ValueError(f"Failed to parse trajectory file: {e}")
        
        # Validate trajectory data
        if 'name' not in traj_data:
            traj_data['name'] = trajectory_path.stem
        
        if 'keyframes' not in traj_data:
            raise ValueError("Trajectory definition must contain 'keyframes'")
        
        # Convert keyframes to CameraKeyframe objects
        keyframes = []
        for kf_data in traj_data['keyframes']:
            keyframe = CameraKeyframe(
                time=kf_data['time'],
                position=tuple(kf_data['position']),
                rotation=tuple(kf_data['rotation']),
                fov=kf_data.get('fov', 90.0)
            )
            keyframes.append(keyframe)
        
        # Calculate duration if not provided
        duration = traj_data.get('duration')
        if duration is None and keyframes:
            duration = max(kf.time for kf in keyframes)
        
        trajectory = TrajectoryDefinition(
            name=traj_data['name'],
            keyframes=keyframes,
            duration=duration or 0.0,
            trajectory_type=traj_data.get('trajectory_type', 'custom')
        )
        
        # Cache the trajectory
        self._trajectory_cache[cache_key] = trajectory
        
        return trajectory
    
    def load_placement_config(self, map_name: str) -> PlacementConfig:
        """
        Load placement configuration for a specific map
        
        Args:
            map_name: Name of the map
            
        Returns:
            PlacementConfig object (default if no specific config found)
        """
        # Check cache first
        if map_name in self._placement_configs:
            return self._placement_configs[map_name]
        
        # Try to find map-specific placement config
        placement_file = self.config_root / f"placement_{map_name}.json"
        if not placement_file.exists():
            placement_file = self.config_root / f"placement_{map_name}.yaml"
        
        if placement_file.exists():
            try:
                if placement_file.suffix.lower() in ['.yaml', '.yml']:
                    with open(placement_file, 'r') as f:
                        placement_data = yaml.safe_load(f)
                else:
                    with open(placement_file, 'r') as f:
                        placement_data = json.load(f)
                
                placement_config = PlacementConfig(
                    min_bounds=tuple(placement_data['min_bounds']),
                    max_bounds=tuple(placement_data['max_bounds']),
                    default_height=placement_data.get('default_height', 0.0),
                    exclusion_zones=placement_data.get('exclusion_zones', [])
                )
                
                self._placement_configs[map_name] = placement_config
                return placement_config
                
            except Exception as e:
                logger.warning(f"Failed to load placement config for {map_name}: {e}")
        
        # Return default placement config
        default_config = PlacementConfig(
            min_bounds=(-100000.0, -100000.0, -1000.0),
            max_bounds=(100000.0, 100000.0, 10000.0),
            default_height=300.0
        )
        
        self._placement_configs[map_name] = default_config
        return default_config
    
    def save_pipeline_config(self, config: PipelineConfig, output_path: Path) -> None:
        """
        Save pipeline configuration to file
        
        Args:
            config: PipelineConfig to save
            output_path: Path where to save the config
        """
        config_data = {
            'maps_directory': str(config.maps_directory),
            'trajectory_file_path': str(config.trajectory_file_path),
            'height_variants': config.height_variants,
            'output_directory': str(config.output_directory),
            'max_retry_attempts': config.max_retry_attempts,
            'scene_stabilization_delay': config.scene_stabilization_delay,
            'target_name_pattern': config.target_name_pattern,
            'occlusion_name_pattern': config.occlusion_name_pattern,
            'sequence_fps': config.sequence_fps,
            'default_camera_fov': config.default_camera_fov
        }
        
        output_path = Path(output_path)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        
        if output_path.suffix.lower() in ['.yaml', '.yml']:
            with open(output_path, 'w') as f:
                yaml.dump(config_data, f, default_flow_style=False, indent=2)
        else:
            with open(output_path, 'w') as f:
                json.dump(config_data, f, indent=2)
    
    def create_default_trajectory_config(self, output_path: Path) -> None:
        """
        Create a default trajectory configuration file
        
        Args:
            output_path: Path where to save the default config
        """
        default_trajectory = {
            'name': 'default_box_trajectory',
            'trajectory_type': 'box',
            'duration': 300.0,
            'keyframes': [
                {
                    'time': 0.0,
                    'position': [0.0, 0.0, 1000.0],
                    'rotation': [0.0, -45.0, 0.0],
                    'fov': 90.0
                },
                {
                    'time': 100.0,
                    'position': [10000.0, 0.0, 1000.0],
                    'rotation': [0.0, -45.0, 90.0],
                    'fov': 90.0
                },
                {
                    'time': 200.0,
                    'position': [10000.0, 10000.0, 1000.0],
                    'rotation': [0.0, -45.0, 180.0],
                    'fov': 90.0
                },
                {
                    'time': 300.0,
                    'position': [0.0, 10000.0, 1000.0],
                    'rotation': [0.0, -45.0, 270.0],
                    'fov': 90.0
                }
            ]
        }
        
        output_path = Path(output_path)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        
        with open(output_path, 'w') as f:
            json.dump(default_trajectory, f, indent=2)
    
    def validate_config_files(self, config: PipelineConfig) -> List[str]:
        """
        Validate all configuration files and dependencies
        
        Args:
            config: PipelineConfig to validate
            
        Returns:
            List of validation error messages (empty if all valid)
        """
        errors = []
        
        # Check maps directory
        if not config.maps_directory.exists():
            errors.append(f"Maps directory does not exist: {config.maps_directory}")
        elif not config.maps_directory.is_dir():
            errors.append(f"Maps directory is not a directory: {config.maps_directory}")
        
        # Check trajectory file
        if not config.trajectory_file_path.exists():
            errors.append(f"Trajectory file does not exist: {config.trajectory_file_path}")
        else:
            try:
                self.load_trajectory_definition(config.trajectory_file_path)
            except Exception as e:
                errors.append(f"Invalid trajectory file: {e}")
        
        # Check output directory (create if doesn't exist)
        try:
            config.output_directory.mkdir(parents=True, exist_ok=True)
        except Exception as e:
            errors.append(f"Cannot create output directory: {e}")
        
        # Validate height variants
        if not config.height_variants:
            errors.append("Height variants list is empty")
        elif any(not isinstance(h, (int, float)) for h in config.height_variants):
            errors.append("All height variants must be numeric")
        
        return errors


def load_config_from_file(config_path: Union[str, Path]) -> PipelineConfig:
    """
    Convenience function to load pipeline configuration from file
    
    Args:
        config_path: Path to configuration file
        
    Returns:
        PipelineConfig object
    """
    config_manager = ConfigManager()
    return config_manager.load_pipeline_config(Path(config_path))


def create_sample_config(output_dir: Path) -> None:
    """
    Create sample configuration files for reference
    
    Args:
        output_dir: Directory where to create sample files
    """
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    
    config_manager = ConfigManager(output_dir)
    
    # Create sample pipeline config
    sample_config = {
        'maps_directory': './Content/Map',
        'trajectory_file_path': f'./{output_dir}/trajectory.json',
        'output_directory': './output',
        'height_variants': [0.0, 500.0, 1000.0, 2000.0],
        'max_retry_attempts': 3,
        'scene_stabilization_delay': 2.0,
        'target_name_pattern': 'Target_*',
        'occlusion_name_pattern': 'SM_*',
        'sequence_fps': 24.0,
        'default_camera_fov': 90.0
    }
    
    with open(output_dir / 'pipeline_config.json', 'w') as f:
        json.dump(sample_config, f, indent=2)
    
    # Create sample trajectory config
    config_manager.create_default_trajectory_config(output_dir / 'trajectory.json')
    
    # Create sample placement config
    sample_placement = {
        'min_bounds': [-50000.0, -50000.0, 0.0],
        'max_bounds': [50000.0, 50000.0, 5000.0],
        'default_height': 300.0,
        'exclusion_zones': []
    }
    
    with open(output_dir / 'placement_sample_map.json', 'w') as f:
        json.dump(sample_placement, f, indent=2)
    
    logger.info(f"Sample configuration files created in: {output_dir}")