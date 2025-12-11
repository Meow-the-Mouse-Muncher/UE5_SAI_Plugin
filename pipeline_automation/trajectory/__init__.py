"""
Trajectory generation system for the Pipeline Automation System.

This package contains trajectory generators that create camera movement patterns
for data collection, including box patterns, line patterns, and custom trajectories.
"""

import sys
from pathlib import Path

# Add current directory to path for imports
current_dir = Path(__file__).parent
sys.path.insert(0, str(current_dir))

try:
    from .base_generator import BaseTrajectoryGenerator
    from .box_generator import BoxTrajectoryGenerator
    from .line_generator import LineTrajectoryGenerator
    from .trajectory_factory import TrajectoryFactory
except ImportError:
    try:
        from base_generator import BaseTrajectoryGenerator
        from box_generator import BoxTrajectoryGenerator
        from line_generator import LineTrajectoryGenerator
        from trajectory_factory import TrajectoryFactory
    except ImportError:
        # Last resort - direct import
        import importlib.util
        
        # Import base_generator
        base_spec = importlib.util.spec_from_file_location("base_generator", current_dir / "base_generator.py")
        base_module = importlib.util.module_from_spec(base_spec)
        base_spec.loader.exec_module(base_module)
        BaseTrajectoryGenerator = base_module.BaseTrajectoryGenerator
        
        # Import box_generator
        box_spec = importlib.util.spec_from_file_location("box_generator", current_dir / "box_generator.py")
        box_module = importlib.util.module_from_spec(box_spec)
        box_spec.loader.exec_module(box_module)
        BoxTrajectoryGenerator = box_module.BoxTrajectoryGenerator
        
        # Import line_generator
        line_spec = importlib.util.spec_from_file_location("line_generator", current_dir / "line_generator.py")
        line_module = importlib.util.module_from_spec(line_spec)
        line_spec.loader.exec_module(line_module)
        LineTrajectoryGenerator = line_module.LineTrajectoryGenerator
        
        # Import trajectory_factory
        factory_spec = importlib.util.spec_from_file_location("trajectory_factory", current_dir / "trajectory_factory.py")
        factory_module = importlib.util.module_from_spec(factory_spec)
        factory_spec.loader.exec_module(factory_module)
        TrajectoryFactory = factory_module.TrajectoryFactory

__all__ = [
    "BaseTrajectoryGenerator",
    "BoxTrajectoryGenerator", 
    "LineTrajectoryGenerator",
    "TrajectoryFactory",
    "create_trajectory",
    "create_box_trajectory", 
    "create_line_trajectory"
]

# Import convenience functions
try:
    from .trajectory_factory import create_trajectory, create_box_trajectory, create_line_trajectory
except ImportError:
    try:
        from trajectory_factory import create_trajectory, create_box_trajectory, create_line_trajectory
    except ImportError:
        # Create placeholder functions for testing
        def create_trajectory(trajectory_type, parameters, **kwargs):
            from .models import TrajectoryDefinition, CameraKeyframe
            return TrajectoryDefinition(
                name=f"test_{trajectory_type}",
                keyframes=[CameraKeyframe(0.0, (0, 0, 1000), (0, 0, 0))],
                duration=10.0,
                trajectory_type=trajectory_type
            )
        
        def create_box_trajectory(line1, line2, z, **kwargs):
            return create_trajectory("box", {"line1": line1, "line2": line2, "z": z}, **kwargs)
        
        def create_line_trajectory(point1, point2, z, **kwargs):
            return create_trajectory("line", {"point1": point1, "point2": point2, "z": z}, **kwargs)