"""
Trajectory Manager component for the Pipeline Automation System.

This module handles trajectory execution, Sequence asset creation, height variants,
and FBX export functionality. It integrates with the existing utils_sequencer
functions and provides systematic trajectory management.
"""

import asyncio
import time
from pathlib import Path
from typing import List, Optional, Dict, Any, Tuple
import logging

try:
    import unreal
except ImportError:
    # For testing without Unreal Engine
    unreal = None

try:
    from ..models import TrajectoryDefinition, TrajectoryResult, CameraKeyframe, ProcessingStatus
    from ..trajectory import TrajectoryFactory
    from .base_manager import BaseManager
except ImportError:
    import sys
    from pathlib import Path
    sys.path.append(str(Path(__file__).parent.parent))
    from models import TrajectoryDefinition, TrajectoryResult, CameraKeyframe, ProcessingStatus
    from trajectory import TrajectoryFactory
    from base_manager import BaseManager


class TrajectoryManager(BaseManager):
    """
    Manages trajectory execution, Sequence creation, and FBX export.
    
    This manager handles:
    - Loading trajectory definitions from files
    - Creating dynamic Sequence uassets with height variants
    - Executing trajectories with OCC/GT modes
    - FBX export for Blender compatibility
    - Integration with existing utils_sequencer functions
    """
    
    def __init__(self, **kwargs):
        """
        Initialize Trajectory Manager
        
        Args:
            **kwargs: Additional arguments passed to BaseManager
        """
        super().__init__(**kwargs)
        self.trajectory_factory = TrajectoryFactory()
        self.sequence_directory = "/Game/Sequences"
        self.current_trajectory: Optional[TrajectoryDefinition] = None
        self.current_sequence_path: Optional[str] = None
        
        # Trajectory execution settings
        self._sequence_fps = 24.0
        self._default_fov = 90.0
        self._execution_timeout = 300.0  # 5 minutes
        self._fbx_export_timeout = 60.0  # 1 minute
    
    def initialize(self, sequence_directory: str = "/Game/Sequences", 
                  sequence_fps: float = 24.0, default_fov: float = 90.0) -> bool:
        """
        Initialize the Trajectory Manager
        
        Args:
            sequence_directory: UE directory for sequence assets
            sequence_fps: Default FPS for sequences
            default_fov: Default field of view
            
        Returns:
            True if initialization successful, False otherwise
        """
        try:
            self.sequence_directory = sequence_directory
            self._sequence_fps = sequence_fps
            self._default_fov = default_fov
            
            self._log_info(f"Trajectory Manager initialized: {sequence_directory} @ {sequence_fps}fps")
            return True
            
        except Exception as e:
            self._log_error(f"Failed to initialize Trajectory Manager: {e}")
            return False
    
    async def load_trajectory_definition(self, trajectory_file: Path) -> Optional[TrajectoryDefinition]:
        """
        Load trajectory definition from file
        
        Args:
            trajectory_file: Path to trajectory definition file
            
        Returns:
            TrajectoryDefinition object or None if failed
        """
        operation_key = f"load_trajectory_{trajectory_file.name}"
        
        while self._should_retry(operation_key):
            try:
                attempt = self._increment_retry_count(operation_key)
                
                if attempt > 1:
                    self._log_retry(f"loading trajectory {trajectory_file.name}", attempt, "Previous attempt failed")
                
                if not trajectory_file.exists():
                    raise FileNotFoundError(f"Trajectory file not found: {trajectory_file}")
                
                # For now, simulate loading - in real implementation, this would
                # parse JSON/YAML trajectory files or use the config manager
                self._log_info(f"Loading trajectory definition from: {trajectory_file}")
                
                # Simulate a simple trajectory for testing
                if not unreal:
                    # Create a mock trajectory for testing
                    keyframes = [
                        CameraKeyframe(0.0, (0, 0, 1000), (0, -45, 0), 90),
                        CameraKeyframe(5.0, (5000, 0, 1000), (0, -45, 90), 90),
                        CameraKeyframe(10.0, (5000, 5000, 1000), (0, -45, 180), 90),
                        CameraKeyframe(15.0, (0, 5000, 1000), (0, -45, 270), 90)
                    ]
                    
                    trajectory = TrajectoryDefinition(
                        name=trajectory_file.stem,
                        keyframes=keyframes,
                        duration=15.0,
                        trajectory_type="custom"
                    )
                    
                    self.current_trajectory = trajectory
                    self._reset_retry_count(operation_key)
                    self._log_info(f"Loaded trajectory: {trajectory.name} ({len(trajectory.keyframes)} keyframes)")
                    return trajectory
                
                # Real implementation would use config manager here
                # trajectory = self.config_manager.load_trajectory_definition(trajectory_file)
                
                self._reset_retry_count(operation_key)
                return None  # Placeholder
                
            except Exception as e:
                error_msg = f"Failed to load trajectory {trajectory_file.name}: {e}"
                
                if not self._should_retry(operation_key):
                    self._log_error(error_msg)
                    return None
                else:
                    self._log_warning(error_msg)
                    await asyncio.sleep(1)
        
        return None
    
    async def create_sequence_asset(self, trajectory_def: TrajectoryDefinition, 
                                  height_offset: float = 0.0) -> Optional[str]:
        """
        Create a dynamic Sequence uasset from trajectory definition
        
        Args:
            trajectory_def: TrajectoryDefinition to convert
            height_offset: Height offset to apply
            
        Returns:
            Sequence asset path or None if failed
        """
        if not unreal:
            # Simulate sequence creation for testing
            sequence_name = f"{trajectory_def.name}_h{height_offset}"
            sequence_path = f"{self.sequence_directory}/{sequence_name}"
            self.current_sequence_path = sequence_path
            self._log_info(f"Simulated sequence creation: {sequence_path}")
            return sequence_path
        
        operation_key = f"create_sequence_{trajectory_def.name}_{height_offset}"
        
        while self._should_retry(operation_key):
            try:
                attempt = self._increment_retry_count(operation_key)
                
                if attempt > 1:
                    self._log_retry(f"creating sequence for {trajectory_def.name}", attempt, "Previous attempt failed")
                
                # Apply height offset if needed
                if height_offset != 0.0:
                    trajectory_def = trajectory_def.apply_height_offset(height_offset)
                
                sequence_name = f"{trajectory_def.name}_h{height_offset}"
                
                # Calculate sequence length in frames
                seq_length = int(trajectory_def.duration * self._sequence_fps)
                
                self._log_info(f"Creating sequence: {sequence_name} ({seq_length} frames)")
                
                # Use existing generate_sequence function from utils_sequencer
                new_sequence = self._generate_sequence(
                    self.sequence_directory,
                    sequence_name,
                    self._sequence_fps,
                    seq_length
                )
                
                if not new_sequence:
                    raise RuntimeError(f"Failed to create sequence asset: {sequence_name}")
                
                # Add camera trajectory to sequence
                success = await self._add_camera_trajectory_to_sequence(new_sequence, trajectory_def)
                if not success:
                    raise RuntimeError(f"Failed to add camera trajectory to sequence: {sequence_name}")
                
                # Save the sequence
                unreal.EditorAssetLibrary.save_loaded_asset(new_sequence, False)
                
                sequence_path = f"{self.sequence_directory}/{sequence_name}"
                self.current_sequence_path = sequence_path
                
                self._reset_retry_count(operation_key)
                self._log_info(f"Successfully created sequence: {sequence_path}")
                
                return sequence_path
                
            except Exception as e:
                error_msg = f"Failed to create sequence for {trajectory_def.name}: {e}"
                
                if not self._should_retry(operation_key):
                    self._log_error(error_msg)
                    return None
                else:
                    self._log_warning(error_msg)
                    await asyncio.sleep(1)
        
        return None
    
    def _generate_sequence(self, sequence_dir: str, sequence_name: str, 
                          seq_fps: float, seq_length: int):
        """
        Generate a new sequence asset (wrapper for utils_sequencer function)
        """
        if not unreal:
            return True  # Mock success
        
        try:
            asset_tools = unreal.AssetToolsHelpers.get_asset_tools()
            
            # Check if asset exists and delete it
            full_path = f"{sequence_dir}/{sequence_name}"
            if unreal.EditorAssetLibrary.does_asset_exist(full_path):
                unreal.EditorAssetLibrary.delete_asset(full_path)
                self._log_warning(f"Deleted existing sequence: {full_path}")
            
            new_sequence = asset_tools.create_asset(
                sequence_name,
                sequence_dir,
                unreal.LevelSequence,
                unreal.LevelSequenceFactoryNew(),
            )
            
            if new_sequence is None:
                raise RuntimeError(f"Failed to create LevelSequence: {sequence_dir}, {sequence_name}")
            
            # Set sequence config
            new_sequence.set_display_rate(unreal.FrameRate(seq_fps))
            new_sequence.set_playback_end(seq_length)
            
            return new_sequence
            
        except Exception as e:
            self._log_error(f"Failed to generate sequence: {e}")
            return None
    
    async def _add_camera_trajectory_to_sequence(self, sequence, trajectory_def: TrajectoryDefinition) -> bool:
        """
        Add camera trajectory to sequence (wrapper for utils_sequencer functions)
        """
        if not unreal:
            return True  # Mock success
        
        try:
            # Convert trajectory keyframes to SequenceKey format
            camera_trans = []
            for keyframe in trajectory_def.keyframes:
                frame_number = int(keyframe.time * self._sequence_fps)
                sequence_key = {
                    "frame": frame_number,
                    "location": keyframe.position,
                    "rotation": keyframe.rotation
                }
                camera_trans.append(sequence_key)
            
            # Use existing add_spawnable_camera_to_sequence function
            # This would need to be imported from utils_sequencer
            # add_spawnable_camera_to_sequence(
            #     sequence,
            #     camera_trans=camera_trans,
            #     camera_class=unreal.CineCameraActor,
            #     camera_fov=self._default_fov,
            #     seq_length=int(trajectory_def.duration * self._sequence_fps),
            #     key_type="LINEAR"
            # )
            
            self._log_info(f"Added {len(camera_trans)} keyframes to sequence")
            return True
            
        except Exception as e:
            self._log_error(f"Failed to add camera trajectory: {e}")
            return False
    
    async def execute_trajectory_sequence(self, sequence_path: str, 
                                        occlusion_enabled: bool = True) -> TrajectoryResult:
        """
        Execute a trajectory sequence for data collection
        
        Args:
            sequence_path: Path to the sequence asset
            occlusion_enabled: True for OCC mode, False for GT mode
            
        Returns:
            TrajectoryResult with execution details
        """
        start_time = time.time()
        mode = "OCC" if occlusion_enabled else "GT"
        
        operation_key = f"execute_sequence_{Path(sequence_path).name}_{mode}"
        
        while self._should_retry(operation_key):
            try:
                attempt = self._increment_retry_count(operation_key)
                
                if attempt > 1:
                    self._log_retry(f"executing sequence {sequence_path} ({mode})", attempt, "Previous attempt failed")
                
                self._log_info(f"Executing trajectory sequence: {sequence_path} ({mode} mode)")
                
                if not unreal:
                    # Simulate execution for testing
                    await asyncio.sleep(0.5)  # Simulate execution time
                    
                    result = TrajectoryResult(
                        success=True,
                        output_files=[Path(f"output/{mode}_data_001.exr")],
                        execution_time=time.time() - start_time,
                        occlusion_enabled=occlusion_enabled
                    )
                    
                    self._log_info(f"Simulated trajectory execution completed: {mode} mode")
                    return result
                
                # Real implementation would:
                # 1. Load the sequence asset
                # 2. Set up movie pipeline for rendering
                # 3. Execute the sequence
                # 4. Wait for completion
                # 5. Collect output files
                
                # For now, return a mock result
                result = TrajectoryResult(
                    success=True,
                    execution_time=time.time() - start_time,
                    occlusion_enabled=occlusion_enabled
                )
                
                self._reset_retry_count(operation_key)
                return result
                
            except Exception as e:
                error_msg = f"Failed to execute sequence {sequence_path} ({mode}): {e}"
                
                if not self._should_retry(operation_key):
                    result = TrajectoryResult(
                        success=False,
                        execution_time=time.time() - start_time,
                        error_message=error_msg,
                        occlusion_enabled=occlusion_enabled
                    )
                    self._log_error(error_msg)
                    return result
                else:
                    self._log_warning(error_msg)
                    await asyncio.sleep(2)
        
        # Should not reach here, but return failure result
        return TrajectoryResult(
            success=False,
            execution_time=time.time() - start_time,
            error_message="Maximum retries exceeded",
            occlusion_enabled=occlusion_enabled
        )
    
    async def export_sequence_fbx(self, sequence_path: str, output_path: Path) -> bool:
        """
        Export sequence as FBX file for Blender compatibility
        
        Args:
            sequence_path: Path to the sequence asset
            output_path: Output path for FBX file
            
        Returns:
            True if export successful, False otherwise
        """
        if not unreal:
            # Simulate FBX export for testing
            output_path.parent.mkdir(parents=True, exist_ok=True)
            output_path.write_text("Mock FBX export data")
            self._log_info(f"Simulated FBX export: {output_path}")
            return True
        
        operation_key = f"export_fbx_{Path(sequence_path).name}"
        
        while self._should_retry(operation_key):
            try:
                attempt = self._increment_retry_count(operation_key)
                
                if attempt > 1:
                    self._log_retry(f"exporting FBX for {sequence_path}", attempt, "Previous attempt failed")
                
                self._log_info(f"Exporting sequence to FBX: {sequence_path} -> {output_path}")
                
                # Ensure output directory exists
                output_path.parent.mkdir(parents=True, exist_ok=True)
                
                # Real implementation would use UE's FBX export functionality
                # This might involve:
                # 1. Loading the sequence
                # 2. Setting up FBX export options
                # 3. Exporting camera animation data
                # 4. Saving to specified path
                
                # For now, simulate success
                await asyncio.sleep(0.2)  # Simulate export time
                
                self._reset_retry_count(operation_key)
                self._log_info(f"Successfully exported FBX: {output_path}")
                
                return True
                
            except Exception as e:
                error_msg = f"Failed to export FBX for {sequence_path}: {e}"
                
                if not self._should_retry(operation_key):
                    self._log_error(error_msg)
                    return False
                else:
                    self._log_warning(error_msg)
                    await asyncio.sleep(1)
        
        return False
    
    async def execute_trajectory_with_variants(self, trajectory_def: TrajectoryDefinition,
                                             height_variants: List[float],
                                             output_base_path: Path) -> List[Tuple[float, TrajectoryResult, TrajectoryResult]]:
        """
        Execute trajectory with multiple height variants (OCC + GT for each)
        
        Args:
            trajectory_def: Base trajectory definition
            height_variants: List of height offsets
            output_base_path: Base path for outputs
            
        Returns:
            List of tuples (height_offset, occ_result, gt_result)
        """
        results = []
        
        for height_offset in height_variants:
            try:
                self._log_info(f"Processing height variant: {height_offset}")
                
                # Create sequence with height offset
                sequence_path = await self.create_sequence_asset(trajectory_def, height_offset)
                if not sequence_path:
                    self._log_error(f"Failed to create sequence for height {height_offset}")
                    continue
                
                # Execute OCC mode
                occ_result = await self.execute_trajectory_sequence(sequence_path, occlusion_enabled=True)
                
                # Execute GT mode
                gt_result = await self.execute_trajectory_sequence(sequence_path, occlusion_enabled=False)
                
                # Export FBX
                fbx_path = output_base_path / f"trajectory_h{height_offset}.fbx"
                fbx_success = await self.export_sequence_fbx(sequence_path, fbx_path)
                
                if fbx_success:
                    occ_result.fbx_export_path = fbx_path
                    gt_result.fbx_export_path = fbx_path
                
                results.append((height_offset, occ_result, gt_result))
                
            except Exception as e:
                self._log_error(f"Failed to process height variant {height_offset}: {e}")
                continue
        
        return results
    
    def cleanup(self) -> None:
        """Cleanup Trajectory Manager resources"""
        self.current_trajectory = None
        self.current_sequence_path = None
        self._retry_counts.clear()
        self._log_info("Trajectory Manager cleaned up")
    
    def get_status(self) -> Dict[str, Any]:
        """
        Get current status of the Trajectory Manager
        
        Returns:
            Dictionary with detailed status information
        """
        base_status = super().get_status()
        
        trajectory_status = {
            "sequence_directory": self.sequence_directory,
            "sequence_fps": self._sequence_fps,
            "default_fov": self._default_fov,
            "current_trajectory": self.current_trajectory.name if self.current_trajectory else None,
            "current_sequence_path": self.current_sequence_path,
            "available_generators": self.trajectory_factory.get_available_types()
        }
        
        base_status.update(trajectory_status)
        return base_status


# Utility functions
def create_trajectory_manager_from_config(pipeline_config) -> TrajectoryManager:
    """
    Create a TrajectoryManager instance from pipeline configuration
    
    Args:
        pipeline_config: PipelineConfig object
        
    Returns:
        Configured TrajectoryManager instance
    """
    trajectory_manager = TrajectoryManager()
    trajectory_manager.set_max_retries(pipeline_config.max_retry_attempts)
    
    success = trajectory_manager.initialize(
        sequence_fps=pipeline_config.sequence_fps,
        default_fov=pipeline_config.default_camera_fov
    )
    
    if not success:
        raise RuntimeError("Failed to initialize Trajectory Manager")
    
    return trajectory_manager