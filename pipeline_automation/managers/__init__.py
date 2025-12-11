"""
Manager components for the Pipeline Automation System.

This package contains all the manager components that handle different aspects
of the pipeline execution, including map management, target management,
trajectory management, and process management.
"""

import sys
from pathlib import Path

# Add current directory to path for imports
current_dir = Path(__file__).parent
sys.path.insert(0, str(current_dir))

try:
    from .map_manager import MapManager
    from .base_manager import BaseManager
    from .target_manager import TargetManager
    from .occlusion_manager import OcclusionManager
    from .trajectory_manager import TrajectoryManager
    from .ue_process_manager import UEProcessManager
    from .output_manager import OutputManager, OutputConfig
except ImportError:
    try:
        from map_manager import MapManager
        from base_manager import BaseManager
        from target_manager import TargetManager
        from occlusion_manager import OcclusionManager
        from trajectory_manager import TrajectoryManager
        from ue_process_manager import UEProcessManager
        from output_manager import OutputManager, OutputConfig
    except ImportError:
        # Last resort - direct import
        import importlib.util
        
        # Import base_manager
        base_spec = importlib.util.spec_from_file_location("base_manager", current_dir / "base_manager.py")
        base_module = importlib.util.module_from_spec(base_spec)
        base_spec.loader.exec_module(base_module)
        BaseManager = base_module.BaseManager
        
        # Import map_manager
        map_spec = importlib.util.spec_from_file_location("map_manager", current_dir / "map_manager.py")
        map_module = importlib.util.module_from_spec(map_spec)
        map_spec.loader.exec_module(map_module)
        MapManager = map_module.MapManager
        
        # Import target_manager
        target_spec = importlib.util.spec_from_file_location("target_manager", current_dir / "target_manager.py")
        target_module = importlib.util.module_from_spec(target_spec)
        target_spec.loader.exec_module(target_module)
        TargetManager = target_module.TargetManager
        
        # Import occlusion_manager
        occlusion_spec = importlib.util.spec_from_file_location("occlusion_manager", current_dir / "occlusion_manager.py")
        occlusion_module = importlib.util.module_from_spec(occlusion_spec)
        occlusion_spec.loader.exec_module(occlusion_module)
        OcclusionManager = occlusion_module.OcclusionManager
        
        # Import trajectory_manager
        trajectory_spec = importlib.util.spec_from_file_location("trajectory_manager", current_dir / "trajectory_manager.py")
        trajectory_module = importlib.util.module_from_spec(trajectory_spec)
        trajectory_spec.loader.exec_module(trajectory_module)
        TrajectoryManager = trajectory_module.TrajectoryManager
        
        # Import ue_process_manager
        ue_spec = importlib.util.spec_from_file_location("ue_process_manager", current_dir / "ue_process_manager.py")
        ue_module = importlib.util.module_from_spec(ue_spec)
        ue_spec.loader.exec_module(ue_module)
        UEProcessManager = ue_module.UEProcessManager
        
        # Import output_manager
        output_spec = importlib.util.spec_from_file_location("output_manager", current_dir / "output_manager.py")
        output_module = importlib.util.module_from_spec(output_spec)
        output_spec.loader.exec_module(output_module)
        OutputManager = output_module.OutputManager
        OutputConfig = output_module.OutputConfig

__all__ = [
    "BaseManager",
    "MapManager",
    "TargetManager",
    "OcclusionManager",
    "TrajectoryManager",
    "UEProcessManager",
    "OutputManager",
    "OutputConfig"
]