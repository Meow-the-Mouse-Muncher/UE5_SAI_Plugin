"""
Core data models for the Pipeline Automation System.

This module defines all the data structures used throughout the pipeline,
including configuration models, runtime models, and result models.
"""

from dataclasses import dataclass, field
from typing import List, Optional, Dict, Any, Union
from pathlib import Path
from enum import Enum
from datetime import datetime
import json


# Type aliases for better readability
Vector3 = tuple[float, float, float]
PathLike = Union[Path, str]


class ProcessingStatus(Enum):
    """Status enumeration for processing states"""
    NOT_STARTED = "not_started"
    IN_PROGRESS = "in_progress" 
    COMPLETED = "completed"
    FAILED = "failed"
    SKIPPED = "skipped"


@dataclass
class PipelineConfig:
    """Main configuration for the pipeline automation system"""
    maps_directory: Path
    trajectory_file_path: Path
    height_variants: List[float]
    output_directory: Path
    max_retry_attempts: int = 3
    scene_stabilization_delay: float = 2.0
    
    # Optional configuration parameters
    target_name_pattern: str = "Target_*"
    occlusion_name_pattern: str = "SM_*"
    sequence_fps: float = 24.0
    default_camera_fov: float = 90.0
    
    # Additional pipeline configuration
    project_path: Optional[Path] = None
    ue_executable_path: Optional[Path] = None
    map_filter: Optional[List[str]] = None
    min_storage_gb: float = 10.0
    
    def __post_init__(self):
        """Validate configuration after initialization"""
        self.maps_directory = Path(self.maps_directory)
        self.trajectory_file_path = Path(self.trajectory_file_path)
        self.output_directory = Path(self.output_directory)
        
        if not self.maps_directory.exists():
            raise ValueError(f"Maps directory does not exist: {self.maps_directory}")
        
        if not self.trajectory_file_path.exists():
            raise ValueError(f"Trajectory file does not exist: {self.trajectory_file_path}")
            
        if self.max_retry_attempts < 0:
            raise ValueError("max_retry_attempts must be non-negative")
            
        if self.scene_stabilization_delay < 0:
            raise ValueError("scene_stabilization_delay must be non-negative")


@dataclass
class MapInfo:
    """Information about a map in the pipeline"""
    name: str
    path: Path
    is_valid: bool = True
    placement_config: Optional[Dict[str, Any]] = None
    processing_status: ProcessingStatus = ProcessingStatus.NOT_STARTED
    error_message: Optional[str] = None
    
    def __post_init__(self):
        """Validate map info after initialization"""
        self.path = Path(self.path)


@dataclass
class PlacementConfig:
    """Configuration for target placement within a map"""
    min_bounds: Vector3
    max_bounds: Vector3
    default_height: float = 0.0
    exclusion_zones: List[Dict[str, Any]] = field(default_factory=list)
    
    def is_position_valid(self, position: Vector3) -> bool:
        """Check if a position is within valid placement bounds"""
        x, y, z = position
        min_x, min_y, min_z = self.min_bounds
        max_x, max_y, max_z = self.max_bounds
        
        return (min_x <= x <= max_x and 
                min_y <= y <= max_y and 
                min_z <= z <= max_z)


@dataclass
class TargetObject:
    """Represents a target object in the scene"""
    name: str
    location: Vector3  # Changed from position to location to match controller
    is_visible: bool = False
    processing_status: ProcessingStatus = ProcessingStatus.NOT_STARTED
    error_message: Optional[str] = None
    
    @property
    def position(self) -> Vector3:
        """Alias for location for backward compatibility"""
        return self.location
    
    @property
    def target_id(self) -> str:
        """Extract target ID from name (e.g., Target_001 -> 001)"""
        if self.name.startswith("Target_"):
            return self.name[7:]  # Remove "Target_" prefix
        return self.name


@dataclass
class OcclusionObject:
    """Represents an occlusion object in the scene"""
    name: str
    position: Vector3
    is_visible: bool = True
    object_type: str = "static_mesh"


@dataclass
class CameraKeyframe:
    """Represents a single camera keyframe in a trajectory"""
    time: float
    position: Vector3
    rotation: Vector3
    fov: float = 90.0
    
    def to_sequence_key(self, frame_number: int) -> Dict[str, Any]:
        """Convert to format compatible with existing SequenceKey"""
        return {
            "frame": frame_number,
            "location": self.position,
            "rotation": self.rotation
        }


@dataclass
class TrajectoryDefinition:
    """Defines a complete camera trajectory"""
    name: str
    keyframes: List[CameraKeyframe]
    duration: float
    trajectory_type: str = "custom"  # box, line, circular, custom
    
    def get_total_frames(self, fps: float) -> int:
        """Calculate total frames based on duration and FPS"""
        return int(self.duration * fps)
    
    def apply_height_offset(self, height_offset: float) -> 'TrajectoryDefinition':
        """Create a new trajectory with height offset applied"""
        offset_keyframes = []
        for keyframe in self.keyframes:
            new_position = (
                keyframe.position[0],
                keyframe.position[1], 
                keyframe.position[2] + height_offset
            )
            offset_keyframes.append(CameraKeyframe(
                time=keyframe.time,
                position=new_position,
                rotation=keyframe.rotation,
                fov=keyframe.fov
            ))
        
        return TrajectoryDefinition(
            name=f"{self.name}_h{height_offset}",
            keyframes=offset_keyframes,
            duration=self.duration,
            trajectory_type=self.trajectory_type
        )


@dataclass
class TrajectoryConfig:
    """Configuration for trajectory generation"""
    trajectory_type: str  # box, line, circular
    parameters: Dict[str, Any]
    height_variants: List[float] = field(default_factory=list)
    
    def validate(self) -> bool:
        """Validate trajectory configuration"""
        valid_types = ["box", "line", "circular", "custom"]
        if self.trajectory_type not in valid_types:
            raise ValueError(f"Invalid trajectory type: {self.trajectory_type}")
        
        required_params = {
            "box": ["line1", "line2", "interval"],
            "line": ["point1", "point2", "yaw"],
            "circular": ["center", "radius", "height"],
            "custom": ["keyframes"]
        }
        
        if self.trajectory_type in required_params:
            for param in required_params[self.trajectory_type]:
                if param not in self.parameters:
                    raise ValueError(f"Missing required parameter '{param}' for trajectory type '{self.trajectory_type}'")
        
        return True


@dataclass
class TrajectoryResult:
    """Result of trajectory execution"""
    success: bool
    output_files: List[Path] = field(default_factory=list)
    fbx_export_path: Optional[Path] = None
    execution_time: float = 0.0
    error_message: Optional[str] = None
    occlusion_enabled: bool = True
    
    def add_output_file(self, file_path: Path) -> None:
        """Add an output file to the result"""
        self.output_files.append(Path(file_path))


@dataclass
class SamplingSession:
    """Represents a complete sampling session for one target"""
    map_info: 'MapInfo'
    target: 'TargetObject'
    height_offset: float
    occ_result: Optional[TrajectoryResult] = None
    gt_result: Optional[TrajectoryResult] = None
    timestamp: Optional[Any] = None  # datetime object
    session_start_time: Optional[float] = None
    session_end_time: Optional[float] = None
    
    @property
    def map_name(self) -> str:
        """Get map name for backward compatibility"""
        return self.map_info.name
    
    @property
    def target_name(self) -> str:
        """Get target name for backward compatibility"""
        return self.target.name
    
    @property
    def is_complete(self) -> bool:
        """Check if both OCC and GT sampling are complete"""
        return (self.occ_result is not None and 
                self.gt_result is not None and
                self.occ_result.success and 
                self.gt_result.success)
    
    @property
    def session_duration(self) -> float:
        """Calculate session duration in seconds"""
        if self.session_start_time and self.session_end_time:
            return self.session_end_time - self.session_start_time
        return 0.0


@dataclass
class PipelineResult:
    """Final result of the entire pipeline execution"""
    success: bool
    total_sessions: int = 0
    successful_sessions: int = 0
    failed_sessions: int = 0
    execution_time: float = 0.0
    start_time: Optional[Any] = None  # datetime object
    end_time: Optional[Any] = None    # datetime object
    error_message: Optional[str] = None
    error_summary: List[str] = field(default_factory=list)
    
    # Legacy fields for backward compatibility
    @property
    def total_sessions_completed(self) -> int:
        return self.total_sessions
    
    @property
    def total_execution_time(self) -> float:
        return self.execution_time
    
    @property
    def success_rate(self) -> float:
        """Calculate success rate as percentage"""
        if self.total_sessions_completed == 0:
            return 0.0
        return (self.successful_sessions / self.total_sessions_completed) * 100.0
    
    def add_error(self, error_message: str) -> None:
        """Add an error to the summary"""
        self.error_summary.append(error_message)
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert result to dictionary for JSON serialization"""
        return {
            "total_maps_processed": self.total_maps_processed,
            "total_targets_processed": self.total_targets_processed,
            "total_sessions_completed": self.total_sessions_completed,
            "successful_sessions": self.successful_sessions,
            "failed_sessions": self.failed_sessions,
            "skipped_sessions": self.skipped_sessions,
            "success_rate": self.success_rate,
            "total_execution_time": self.total_execution_time,
            "output_directory": str(self.output_directory),
            "error_summary": self.error_summary
        }
    
    def save_to_file(self, file_path: Path) -> None:
        """Save result to JSON file"""
        with open(file_path, 'w') as f:
            json.dump(self.to_dict(), f, indent=2)


# Utility functions for model creation
def create_default_pipeline_config(
    maps_dir: PathLike,
    trajectory_file: PathLike,
    output_dir: PathLike,
    height_variants: Optional[List[float]] = None
) -> PipelineConfig:
    """Create a default pipeline configuration"""
    if height_variants is None:
        height_variants = [0.0, 500.0, 1000.0]  # Default height variants
    
    return PipelineConfig(
        maps_directory=Path(maps_dir),
        trajectory_file_path=Path(trajectory_file),
        height_variants=height_variants,
        output_directory=Path(output_dir)
    )


def create_box_trajectory_config(
    line1: List[float],
    line2: List[float], 
    interval: float = 800.0,
    height_variants: Optional[List[float]] = None
) -> TrajectoryConfig:
    """Create a box trajectory configuration"""
    return TrajectoryConfig(
        trajectory_type="box",
        parameters={
            "line1": line1,
            "line2": line2,
            "interval": interval
        },
        height_variants=height_variants or []
    )


def create_line_trajectory_config(
    point1: List[float],
    point2: List[float],
    yaw: float = 0.0,
    height_variants: Optional[List[float]] = None
) -> TrajectoryConfig:
    """Create a line trajectory configuration"""
    return TrajectoryConfig(
        trajectory_type="line",
        parameters={
            "point1": point1,
            "point2": point2,
            "yaw": yaw
        },
        height_variants=height_variants or []
    )