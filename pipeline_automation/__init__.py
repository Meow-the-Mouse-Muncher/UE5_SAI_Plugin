"""
Pipeline Automation System

An automated multi-map data collection pipeline system that extends the existing 
MatrixCityPlugin to perform systematic sampling across multiple maps, targets, 
and trajectories.
"""

__version__ = "1.0.0"
__author__ = "Pipeline Automation Team"

try:
    from .models import *
    from .config import *
    from .progress import *
    from .managers import *
    from .trajectory import *
    from .pipeline_controller import PipelineController, PipelineState, run_pipeline_from_config, create_pipeline_controller
    from .config_templates import ConfigTemplates, ConfigGenerator, ConfigValidator, create_default_configs, validate_config, create_custom_trajectory
    from .integration_tests import IntegrationTester, run_integration_tests
except ImportError:
    from models import *
    from config import *
    from progress import *
    from managers import *
    from trajectory import *
    from pipeline_controller import PipelineController, PipelineState, run_pipeline_from_config, create_pipeline_controller
    from config_templates import ConfigTemplates, ConfigGenerator, ConfigValidator, create_default_configs, validate_config, create_custom_trajectory
    from integration_tests import IntegrationTester, run_integration_tests

__all__ = [
    # Core data models
    "PipelineConfig",
    "MapInfo", 
    "TargetObject",
    "OcclusionObject",
    "TrajectoryConfig",
    "TrajectoryDefinition",
    "CameraKeyframe",
    "TrajectoryResult",
    "ProgressStatus",
    "SamplingSession",
    "PipelineResult",
    "PlacementConfig",
    "ProcessingStatus",
    
    # Configuration management
    "ConfigManager",
    "load_config_from_file",
    "create_sample_config",
    
    # Progress tracking and logging
    "ProgressTracker",
    "PipelineLogger",
    
    # Manager components
    "BaseManager",
    "MapManager",
    "TargetManager",
    "OcclusionManager",
    "TrajectoryManager",
    "UEProcessManager",
    "OutputManager",
    "OutputConfig",
    
    # Trajectory generation
    "BaseTrajectoryGenerator",
    "BoxTrajectoryGenerator",
    "LineTrajectoryGenerator", 
    "TrajectoryFactory",
    
    # Pipeline Controller
    "PipelineController",
    "PipelineState",
    "run_pipeline_from_config",
    "create_pipeline_controller",
    
    # Configuration Templates
    "ConfigTemplates",
    "ConfigGenerator", 
    "ConfigValidator",
    "create_default_configs",
    "validate_config",
    "create_custom_trajectory",
    
    # Integration Testing
    "IntegrationTester",
    "run_integration_tests",
    
    # Utility functions
    "create_default_pipeline_config",
    "create_box_trajectory_config",
    "create_line_trajectory_config"
]