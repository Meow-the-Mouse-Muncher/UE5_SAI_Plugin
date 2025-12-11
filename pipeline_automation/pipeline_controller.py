"""
Pipeline Controller for the Pipeline Automation System.

This is the main orchestration component that coordinates all managers
and implements the map→target→trajectory execution loop with state
management and error recovery.
"""

import asyncio
import signal
import time
from pathlib import Path
from typing import Dict, List, Optional, Tuple, Any
from dataclasses import dataclass
from datetime import datetime
import logging

try:
    from .models import (
        PipelineConfig, MapInfo, TargetObject, TrajectoryDefinition,
        SamplingSession, PipelineResult, ProcessingStatus
    )
    from .managers import (
        MapManager, TargetManager, OcclusionManager, TrajectoryManager,
        UEProcessManager, OutputManager, OutputConfig
    )
    from .progress import ProgressTracker
    from .config import ConfigManager
except ImportError:
    import sys
    sys.path.append(str(Path(__file__).parent))
    from models import (
        PipelineConfig, MapInfo, TargetObject, TrajectoryDefinition,
        SamplingSession, PipelineResult, ProcessingStatus
    )
    from managers import (
        MapManager, TargetManager, OcclusionManager, TrajectoryManager,
        UEProcessManager, OutputManager, OutputConfig
    )
    from progress import ProgressTracker
    from config import ConfigManager


@dataclass
class PipelineState:
    """Current state of the pipeline execution."""
    current_map_index: int = 0
    current_target_index: int = 0
    current_height_index: int = 0
    total_maps: int = 0
    total_targets: int = 0
    total_heights: int = 0
    start_time: Optional[datetime] = None
    is_paused: bool = False
    is_cancelled: bool = False
    last_checkpoint: Optional[datetime] = None


class PipelineController:
    """
    Main controller that orchestrates the entire pipeline execution.
    
    Coordinates all manager components and implements the main execution loop:
    map → target → trajectory (with height variants and OCC/GT modes)
    """
    
    def __init__(self, config: PipelineConfig):
        """
        Initialize Pipeline Controller
        
        Args:
            config: Pipeline configuration
        """
        self.config = config
        self.logger = logging.getLogger("PipelineController")
        
        # Initialize state
        self.state = PipelineState()
        self._shutdown_requested = False
        self._pause_requested = False
        
        # Initialize managers
        self._initialize_managers()
        
        # Progress tracking
        self.progress_tracker = ProgressTracker()
        
        # Results storage
        self.session_results: List[SamplingSession] = []
        self.failed_sessions: List[Dict[str, Any]] = []
        
        # Signal handlers for graceful shutdown
        self._setup_signal_handlers()
    
    def _initialize_managers(self) -> None:
        """Initialize all manager components."""
        try:
            # Create config manager
            config_manager = ConfigManager()
            
            # Map Manager
            self.map_manager = MapManager(config_manager)
            self.map_manager.set_max_retries(self.config.max_retry_attempts)
            self.map_manager.initialize(self.config.maps_directory)
            
            # Target Manager
            self.target_manager = TargetManager()
            self.target_manager.set_max_retries(self.config.max_retry_attempts)
            
            # Occlusion Manager
            self.occlusion_manager = OcclusionManager()
            self.occlusion_manager.set_max_retries(self.config.max_retry_attempts)
            
            # Trajectory Manager
            self.trajectory_manager = TrajectoryManager()
            self.trajectory_manager.set_max_retries(self.config.max_retry_attempts)
            self.trajectory_manager.initialize(
                sequence_fps=self.config.sequence_fps,
                default_fov=self.config.default_camera_fov
            )
            
            # UE Process Manager (only if paths are provided)
            if self.config.project_path and self.config.ue_executable_path:
                self.ue_process_manager = UEProcessManager(
                    project_path=self.config.project_path,
                    ue_executable_path=self.config.ue_executable_path
                )
            else:
                # Create a mock UE process manager for testing
                self.ue_process_manager = None
                self.logger.warning("UE Process Manager not initialized - missing paths")
            
            # Output Manager
            output_config = OutputConfig(
                base_output_dir=str(self.config.output_directory),
                min_free_space_gb=self.config.min_storage_gb
            )
            self.output_manager = OutputManager(output_config)
            
            self.logger.info("All managers initialized successfully")
            
        except Exception as e:
            self.logger.error(f"Failed to initialize managers: {e}")
            raise
    
    def _setup_signal_handlers(self) -> None:
        """Setup signal handlers for graceful shutdown."""
        def signal_handler(signum, frame):
            self.logger.info(f"Received signal {signum}, initiating graceful shutdown...")
            self._shutdown_requested = True
        
        signal.signal(signal.SIGINT, signal_handler)
        signal.signal(signal.SIGTERM, signal_handler)
    
    async def run_pipeline(self) -> PipelineResult:
        """
        Run the complete pipeline execution.
        
        Returns:
            PipelineResult with execution summary
        """
        self.logger.info("Starting Pipeline Automation System")
        self.state.start_time = datetime.now()
        
        try:
            # Initialize UE process
            if not await self._initialize_ue_process():
                return self._create_failure_result("Failed to initialize Unreal Engine")
            
            # Discover maps
            maps = await self._discover_maps()
            if not maps:
                return self._create_failure_result("No maps found for processing")
            
            self.state.total_maps = len(maps)
            self.logger.info(f"Found {len(maps)} maps to process")
            
            # Main execution loop
            for map_index, map_info in enumerate(maps):
                if self._shutdown_requested:
                    break
                
                self.state.current_map_index = map_index
                await self._process_map(map_info)
            
            # Generate final results
            result = self._create_success_result()
            
            # Create completion report
            await self._create_completion_report()
            
            self.logger.info("Pipeline execution completed successfully")
            return result
            
        except Exception as e:
            self.logger.error(f"Pipeline execution failed: {e}")
            return self._create_failure_result(str(e))
        
        finally:
            await self._cleanup()
    
    async def _initialize_ue_process(self) -> bool:
        """Initialize Unreal Engine process."""
        try:
            if self.ue_process_manager is None:
                self.logger.warning("UE Process Manager not available - running in test mode")
                return True  # Allow testing without UE
            
            self.logger.info("Initializing Unreal Engine process...")
            
            # Start UE process
            success = await self.ue_process_manager.start_ue_process()
            if not success:
                self.logger.error("Failed to start Unreal Engine process")
                return False
            
            # Wait for UE to be ready
            ready = await self.ue_process_manager.wait_for_ue_ready(timeout=120)
            if not ready:
                self.logger.error("Unreal Engine failed to become ready")
                return False
            
            self.logger.info("Unreal Engine initialized successfully")
            return True
            
        except Exception as e:
            self.logger.error(f"Failed to initialize UE process: {e}")
            return False
    
    async def _discover_maps(self) -> List[MapInfo]:
        """Discover all maps to process."""
        try:
            maps = await self.map_manager.discover_maps()
            
            # Filter maps if specified in config
            if self.config.map_filter:
                filtered_maps = []
                for map_info in maps:
                    if any(pattern in map_info.name for pattern in self.config.map_filter):
                        filtered_maps.append(map_info)
                maps = filtered_maps
            
            # Sort maps alphabetically
            maps.sort(key=lambda m: m.name)
            
            return maps
            
        except Exception as e:
            self.logger.error(f"Failed to discover maps: {e}")
            return []
    
    async def _process_map(self, map_info: MapInfo) -> None:
        """Process a single map with all its targets."""
        self.logger.info(f"Processing map: {map_info.name}")
        
        try:
            # Load map
            if not await self.map_manager.load_map(map_info):
                self.logger.error(f"Failed to load map: {map_info.name}")
                return
            
            # Discover targets in the map
            targets = await self.target_manager.discover_targets()
            if not targets:
                self.logger.warning(f"No targets found in map: {map_info.name}")
                return
            
            self.state.total_targets = len(targets)
            self.logger.info(f"Found {len(targets)} targets in {map_info.name}")
            
            # Process each target
            for target_index, target in enumerate(targets):
                if self._shutdown_requested:
                    break
                
                self.state.current_target_index = target_index
                await self._process_target(map_info, target)
            
        except Exception as e:
            self.logger.error(f"Failed to process map {map_info.name}: {e}")
    
    async def _process_target(self, map_info: MapInfo, target: TargetObject) -> None:
        """Process a single target with all height variants."""
        self.logger.info(f"Processing target: {target.name}")
        
        try:
            # Set target visibility (hide others, show current)
            if not await self.target_manager.set_target_visibility(target, visible=True):
                self.logger.error(f"Failed to set target visibility: {target.name}")
                return
            
            # Process each height variant
            height_variants = self.config.height_variants or [0.0]
            self.state.total_heights = len(height_variants)
            
            for height_index, height_offset in enumerate(height_variants):
                if self._shutdown_requested:
                    break
                
                self.state.current_height_index = height_index
                await self._process_height_variant(map_info, target, height_offset)
            
        except Exception as e:
            self.logger.error(f"Failed to process target {target.name}: {e}")
    
    async def _process_height_variant(self, map_info: MapInfo, target: TargetObject, 
                                    height_offset: float) -> None:
        """Process a single height variant with OCC and GT modes."""
        self.logger.info(f"Processing height variant: {height_offset}")
        
        try:
            # Create output directories
            directories = self.output_manager.create_session_directory(
                map_info, target, height_offset, "OCC"
            )
            
            # Load trajectory definition
            trajectory_def = await self._get_trajectory_definition(map_info, target)
            if not trajectory_def:
                self.logger.error("Failed to get trajectory definition")
                return
            
            # Process OCC mode (with occlusions)
            occ_result = await self._execute_sampling_mode(
                map_info, target, height_offset, trajectory_def, 
                occlusion_enabled=True
            )
            
            # Process GT mode (without occlusions)
            gt_result = await self._execute_sampling_mode(
                map_info, target, height_offset, trajectory_def,
                occlusion_enabled=False
            )
            
            # Create session result
            session = SamplingSession(
                map_info=map_info,
                target=target,
                height_offset=height_offset,
                occ_result=occ_result,
                gt_result=gt_result,
                timestamp=datetime.now()
            )
            
            self.session_results.append(session)
            
            # Update progress
            self._update_progress()
            
        except Exception as e:
            self.logger.error(f"Failed to process height variant {height_offset}: {e}")
            self._record_failed_session(map_info, target, height_offset, str(e))
    
    async def _execute_sampling_mode(self, map_info: MapInfo, target: TargetObject,
                                   height_offset: float, trajectory_def: TrajectoryDefinition,
                                   occlusion_enabled: bool) -> Any:
        """Execute sampling for a specific mode (OCC or GT)."""
        mode = "OCC" if occlusion_enabled else "GT"
        self.logger.info(f"Executing {mode} mode sampling")
        
        try:
            # Set occlusion state
            if occlusion_enabled:
                await self.occlusion_manager.show_all_occlusions()
            else:
                await self.occlusion_manager.hide_all_occlusions()
            
            # Wait for scene stabilization
            await asyncio.sleep(self.config.scene_stabilization_delay)
            
            # Create sequence with height offset
            sequence_path = await self.trajectory_manager.create_sequence_asset(
                trajectory_def, height_offset
            )
            
            if not sequence_path:
                raise RuntimeError(f"Failed to create sequence for {mode} mode")
            
            # Execute trajectory
            result = await self.trajectory_manager.execute_trajectory_sequence(
                sequence_path, occlusion_enabled
            )
            
            # Export FBX if successful
            if result.success:
                fbx_path = self.output_manager.get_full_filepath(
                    map_info, target, height_offset, mode, "fbx"
                )
                await self.trajectory_manager.export_sequence_fbx(sequence_path, fbx_path)
                result.fbx_export_path = fbx_path
            
            return result
            
        except Exception as e:
            self.logger.error(f"Failed to execute {mode} mode: {e}")
            # Return a failure result
            from .models import TrajectoryResult
            return TrajectoryResult(
                success=False,
                error_message=str(e),
                occlusion_enabled=occlusion_enabled
            )
    
    async def _get_trajectory_definition(self, map_info: MapInfo, 
                                       target: TargetObject) -> Optional[TrajectoryDefinition]:
        """Get trajectory definition for the current map/target."""
        try:
            # For now, use a default trajectory
            # In a real implementation, this would load from config files
            # or generate based on map/target characteristics
            
            # Create a simple box trajectory around the target
            from .trajectory import create_box_trajectory
            
            # Define box around target
            target_pos = target.location
            box_size = 1000  # 10m box around target
            
            line1 = [target_pos[0] - box_size, target_pos[1] - box_size,
                    target_pos[0] + box_size, target_pos[1] - box_size]
            line2 = [target_pos[0] - box_size, target_pos[1] + box_size,
                    target_pos[0] + box_size, target_pos[1] + box_size]
            
            trajectory = create_box_trajectory(
                line1=line1,
                line2=line2,
                z=target_pos[2] + 500,  # 5m above target
                mode="train"
            )
            
            return trajectory
            
        except Exception as e:
            self.logger.error(f"Failed to get trajectory definition: {e}")
            return None
    
    def _update_progress(self) -> None:
        """Update progress tracking."""
        try:
            total_sessions = (self.state.total_maps * 
                            self.state.total_targets * 
                            self.state.total_heights)
            
            completed_sessions = len(self.session_results)
            
            if total_sessions > 0:
                progress_percent = (completed_sessions / total_sessions) * 100
                
                self.progress_tracker.update_progress(
                    current_step=completed_sessions,
                    total_steps=total_sessions,
                    current_operation=f"Map {self.state.current_map_index + 1}/{self.state.total_maps}"
                )
                
                self.logger.info(f"Progress: {progress_percent:.1f}% "
                               f"({completed_sessions}/{total_sessions} sessions)")
            
        except Exception as e:
            self.logger.error(f"Failed to update progress: {e}")
    
    def _record_failed_session(self, map_info: MapInfo, target: TargetObject,
                             height_offset: float, error: str) -> None:
        """Record a failed session for reporting."""
        failed_session = {
            "map_name": map_info.name,
            "target_name": target.name,
            "height_offset": height_offset,
            "error": error,
            "timestamp": datetime.now().isoformat()
        }
        self.failed_sessions.append(failed_session)
    
    async def _create_completion_report(self) -> None:
        """Create final completion report."""
        try:
            # Generate session summaries
            session_summaries = []
            for session in self.session_results:
                summary = self.output_manager.get_session_summary(
                    session.map_info, session.target, session.height_offset
                )
                session_summaries.append(summary)
            
            # Create completion report
            report_path = self.output_manager.create_completion_report(session_summaries)
            
            # Add failure information to report
            if self.failed_sessions:
                with open(report_path, 'a') as f:
                    f.write(f"\n\nFailed Sessions ({len(self.failed_sessions)}):\n")
                    f.write("-" * 30 + "\n")
                    for failed in self.failed_sessions:
                        f.write(f"Map: {failed['map_name']}, "
                               f"Target: {failed['target_name']}, "
                               f"Height: {failed['height_offset']}\n")
                        f.write(f"Error: {failed['error']}\n")
                        f.write(f"Time: {failed['timestamp']}\n\n")
            
            self.logger.info(f"Completion report created: {report_path}")
            
        except Exception as e:
            self.logger.error(f"Failed to create completion report: {e}")
    
    def _create_success_result(self) -> PipelineResult:
        """Create successful pipeline result."""
        end_time = datetime.now()
        execution_time = (end_time - self.state.start_time).total_seconds()
        
        return PipelineResult(
            success=True,
            total_sessions=len(self.session_results),
            successful_sessions=len(self.session_results),
            failed_sessions=len(self.failed_sessions),
            execution_time=execution_time,
            start_time=self.state.start_time,
            end_time=end_time
        )
    
    def _create_failure_result(self, error_message: str) -> PipelineResult:
        """Create failed pipeline result."""
        end_time = datetime.now()
        execution_time = 0
        if self.state.start_time:
            execution_time = (end_time - self.state.start_time).total_seconds()
        
        return PipelineResult(
            success=False,
            error_message=error_message,
            total_sessions=len(self.session_results) + len(self.failed_sessions),
            successful_sessions=len(self.session_results),
            failed_sessions=len(self.failed_sessions),
            execution_time=execution_time,
            start_time=self.state.start_time,
            end_time=end_time
        )
    
    async def _cleanup(self) -> None:
        """Cleanup resources and managers."""
        try:
            self.logger.info("Cleaning up pipeline resources...")
            
            # Cleanup managers
            if hasattr(self, 'trajectory_manager'):
                self.trajectory_manager.cleanup()
            
            if hasattr(self, 'ue_process_manager') and self.ue_process_manager is not None:
                await self.ue_process_manager.cleanup()
            
            # Save state for potential resumption
            await self._save_pipeline_state()
            
            self.logger.info("Pipeline cleanup completed")
            
        except Exception as e:
            self.logger.error(f"Error during cleanup: {e}")
    
    async def _save_pipeline_state(self) -> None:
        """Save current pipeline state for resumption."""
        try:
            state_file = Path(self.config.output_directory) / "pipeline_state.json"
            state_file.parent.mkdir(parents=True, exist_ok=True)
            
            state_data = {
                "current_map_index": self.state.current_map_index,
                "current_target_index": self.state.current_target_index,
                "current_height_index": self.state.current_height_index,
                "completed_sessions": len(self.session_results),
                "failed_sessions": len(self.failed_sessions),
                "last_checkpoint": datetime.now().isoformat(),
                "shutdown_requested": self._shutdown_requested
            }
            
            import json
            with open(state_file, 'w') as f:
                json.dump(state_data, f, indent=2)
            
            self.logger.info(f"Pipeline state saved: {state_file}")
            
        except Exception as e:
            self.logger.error(f"Failed to save pipeline state: {e}")
    
    def pause_pipeline(self) -> None:
        """Pause pipeline execution."""
        self._pause_requested = True
        self.state.is_paused = True
        self.logger.info("Pipeline pause requested")
    
    def resume_pipeline(self) -> None:
        """Resume pipeline execution."""
        self._pause_requested = False
        self.state.is_paused = False
        self.logger.info("Pipeline resumed")
    
    def stop_pipeline(self) -> None:
        """Stop pipeline execution gracefully."""
        self._shutdown_requested = True
        self.logger.info("Pipeline stop requested")
    
    def get_status(self) -> Dict[str, Any]:
        """Get current pipeline status."""
        return {
            "state": {
                "current_map_index": self.state.current_map_index,
                "current_target_index": self.state.current_target_index,
                "current_height_index": self.state.current_height_index,
                "total_maps": self.state.total_maps,
                "total_targets": self.state.total_targets,
                "total_heights": self.state.total_heights,
                "is_paused": self.state.is_paused,
                "is_cancelled": self._shutdown_requested
            },
            "progress": {
                "completed_sessions": len(self.session_results),
                "failed_sessions": len(self.failed_sessions),
                "start_time": self.state.start_time.isoformat() if self.state.start_time else None
            },
            "managers": {
                "map_manager": self.map_manager.get_status(),
                "target_manager": self.target_manager.get_status(),
                "trajectory_manager": self.trajectory_manager.get_status(),
                "ue_process_manager": self.ue_process_manager.get_status() if self.ue_process_manager else {"status": "not_initialized"}
            }
        }


# Utility functions
async def run_pipeline_from_config(config_path: Path) -> PipelineResult:
    """
    Run pipeline from configuration file.
    
    Args:
        config_path: Path to pipeline configuration file
        
    Returns:
        PipelineResult with execution summary
    """
    try:
        # Load configuration
        config_manager = ConfigManager()
        config = config_manager.load_config(config_path)
        
        # Create and run pipeline
        controller = PipelineController(config)
        result = await controller.run_pipeline()
        
        return result
        
    except Exception as e:
        logging.error(f"Failed to run pipeline from config: {e}")
        raise


def create_pipeline_controller(config: PipelineConfig) -> PipelineController:
    """
    Create a PipelineController instance.
    
    Args:
        config: Pipeline configuration
        
    Returns:
        Configured PipelineController instance
    """
    return PipelineController(config)