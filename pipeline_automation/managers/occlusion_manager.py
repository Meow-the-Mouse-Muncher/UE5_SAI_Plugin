"""
Occlusion Manager component for the Pipeline Automation System.

This module handles SM_* occlusion object discovery and visibility control.
It manages occlusion objects to generate both occluded (OCC) and ground truth (GT) datasets.
"""

import asyncio
import time
from pathlib import Path
from typing import List, Optional, Dict, Any, Tuple
import logging
import fnmatch

try:
    import unreal
except ImportError:
    # For testing without Unreal Engine
    unreal = None

try:
    from ..models import OcclusionObject, ProcessingStatus
    from .base_manager import BaseManager
except ImportError:
    import sys
    from pathlib import Path
    sys.path.append(str(Path(__file__).parent.parent))
    from models import OcclusionObject, ProcessingStatus
    from base_manager import BaseManager


class OcclusionManager(BaseManager):
    """
    Manages SM_* occlusion object discovery and visibility control.
    
    This manager handles:
    - Discovery of SM_* occlusion objects in the current scene
    - Batch visibility control for OCC/GT data generation
    - Scene state management and restoration
    - Scene stabilization waiting and error handling
    """
    
    def __init__(self, **kwargs):
        """
        Initialize Occlusion Manager
        
        Args:
            **kwargs: Additional arguments passed to BaseManager
        """
        super().__init__(**kwargs)
        self.occlusion_name_pattern = "SM_*"
        self.discovered_occlusions: List[OcclusionObject] = []
        self.original_visibility_states: Dict[str, bool] = {}
        self.current_occlusion_state = True  # True = visible (OCC mode), False = hidden (GT mode)
        
        # Occlusion management settings
        self._discovery_timeout = 30.0  # seconds
        self._scene_stabilization_delay = 2.0  # seconds
        self._batch_operation_delay = 0.1  # seconds between individual operations
    
    def initialize(self, occlusion_name_pattern: str = "SM_*", scene_stabilization_delay: float = 2.0) -> bool:
        """
        Initialize the Occlusion Manager
        
        Args:
            occlusion_name_pattern: Pattern for occlusion object names (e.g., "SM_*")
            scene_stabilization_delay: Time to wait for scene stabilization after changes
            
        Returns:
            True if initialization successful, False otherwise
        """
        try:
            self.occlusion_name_pattern = occlusion_name_pattern
            self._scene_stabilization_delay = scene_stabilization_delay
            self._log_info(f"Occlusion Manager initialized with pattern: {occlusion_name_pattern}")
            return True
            
        except Exception as e:
            self._log_error(f"Failed to initialize Occlusion Manager: {e}")
            return False
    
    async def discover_occlusion_objects(self) -> List[OcclusionObject]:
        """
        Discover all SM_* occlusion objects in the current scene
        
        Returns:
            List of discovered OcclusionObject instances
        """
        if not unreal:
            # Simulate discovery for testing
            self._log_warning("Unreal Engine not available, simulating occlusion discovery")
            mock_occlusions = [
                OcclusionObject(name="SM_Building_001", position=(2000.0, 3000.0, 0.0)),
                OcclusionObject(name="SM_Tree_Large_002", position=(4000.0, 5000.0, 0.0)),
                OcclusionObject(name="SM_Wall_Section_003", position=(6000.0, 7000.0, 0.0))
            ]
            self.discovered_occlusions = mock_occlusions
            return mock_occlusions
        
        operation_key = "discover_occlusions"
        
        while self._should_retry(operation_key):
            try:
                attempt = self._increment_retry_count(operation_key)
                
                if attempt > 1:
                    self._log_retry("occlusion discovery", attempt, "Previous attempt failed")
                
                self.discovered_occlusions.clear()
                
                # Get all actors in the scene
                editor_actor_subsystem = unreal.get_editor_subsystem(unreal.EditorActorSubsystem)
                all_actors = editor_actor_subsystem.get_all_level_actors()
                
                if not all_actors:
                    self._log_warning("No actors found in the scene")
                    return []
                
                # Filter actors by name pattern
                occlusion_actors = []
                for actor in all_actors:
                    actor_label = actor.get_actor_label()
                    if fnmatch.fnmatch(actor_label, self.occlusion_name_pattern):
                        occlusion_actors.append(actor)
                
                if not occlusion_actors:
                    self._log_warning(f"No occlusion objects found matching pattern: {self.occlusion_name_pattern}")
                    return []
                
                # Sort occlusion objects by name for consistent processing
                occlusion_actors.sort(key=lambda actor: actor.get_actor_label())
                
                # Create OcclusionObject instances
                for actor in occlusion_actors:
                    try:
                        actor_label = actor.get_actor_label()
                        location = actor.get_actor_location()
                        position = (location.x, location.y, location.z)
                        
                        # Check if actor is currently visible
                        is_visible = not actor.is_hidden_ed()
                        
                        # Determine object type
                        object_type = "static_mesh"
                        if hasattr(actor, 'static_mesh_component'):
                            object_type = "static_mesh"
                        elif hasattr(actor, 'skeletal_mesh_component'):
                            object_type = "skeletal_mesh"
                        
                        occlusion_obj = OcclusionObject(
                            name=actor_label,
                            position=position,
                            is_visible=is_visible,
                            object_type=object_type
                        )
                        
                        self.discovered_occlusions.append(occlusion_obj)
                        
                        # Store original visibility state
                        self.original_visibility_states[actor_label] = is_visible
                        
                    except Exception as e:
                        self._log_warning(f"Failed to process occlusion actor {actor.get_actor_label()}: {e}")
                        continue
                
                self._reset_retry_count(operation_key)
                self._log_info(f"Discovered {len(self.discovered_occlusions)} occlusion objects: {[o.name for o in self.discovered_occlusions[:5]]}{'...' if len(self.discovered_occlusions) > 5 else ''}")
                
                return self.discovered_occlusions
                
            except Exception as e:
                error_msg = f"Failed to discover occlusion objects: {e}"
                
                if not self._should_retry(operation_key):
                    self._log_error(error_msg)
                    return []
                else:
                    self._log_warning(error_msg)
                    await asyncio.sleep(1)  # Brief delay before retry
        
        return []
    
    async def set_occlusion_state(self, visible: bool) -> bool:
        """
        Set visibility state for all occlusion objects
        
        Args:
            visible: True for OCC mode (occlusions visible), False for GT mode (occlusions hidden)
            
        Returns:
            True if successful, False otherwise
        """
        if not self.discovered_occlusions:
            self._log_warning("No occlusion objects discovered yet")
            return False
        
        operation_key = f"set_occlusion_state_{visible}"
        
        while self._should_retry(operation_key):
            try:
                attempt = self._increment_retry_count(operation_key)
                
                if attempt > 1:
                    self._log_retry(f"setting occlusion state to {visible}", attempt, "Previous attempt failed")
                
                mode_name = "OCC" if visible else "GT"
                self._log_info(f"Setting occlusion state to {mode_name} mode ({len(self.discovered_occlusions)} objects)")
                
                success_count = 0
                
                # Process occlusion objects in batches to avoid overwhelming the system
                for i, occlusion in enumerate(self.discovered_occlusions):
                    try:
                        if await self._set_single_occlusion_visibility(occlusion, visible):
                            success_count += 1
                        
                        # Small delay between operations to prevent system overload
                        if i < len(self.discovered_occlusions) - 1:
                            await asyncio.sleep(self._batch_operation_delay)
                            
                    except Exception as e:
                        self._log_warning(f"Failed to set visibility for {occlusion.name}: {e}")
                        continue
                
                # Wait for scene stabilization
                self._log_info(f"Waiting {self._scene_stabilization_delay}s for scene stabilization...")
                await self.wait_for_scene_stabilization()
                
                # Update current state
                self.current_occlusion_state = visible
                
                if success_count == len(self.discovered_occlusions):
                    self._reset_retry_count(operation_key)
                    self._log_info(f"Successfully set occlusion state to {mode_name} mode ({success_count}/{len(self.discovered_occlusions)})")
                    return True
                else:
                    raise RuntimeError(f"Partial failure: {success_count}/{len(self.discovered_occlusions)} objects processed")
                
            except Exception as e:
                error_msg = f"Failed to set occlusion state: {e}"
                
                if not self._should_retry(operation_key):
                    self._log_error(error_msg)
                    return False
                else:
                    self._log_warning(error_msg)
                    await asyncio.sleep(1)  # Brief delay before retry
        
        return False
    
    async def _set_single_occlusion_visibility(self, occlusion: OcclusionObject, visible: bool) -> bool:
        """
        Set visibility for a single occlusion object
        
        Args:
            occlusion: OcclusionObject to modify
            visible: True to show, False to hide
            
        Returns:
            True if successful, False otherwise
        """
        if not unreal:
            # Simulate visibility change for testing
            occlusion.is_visible = visible
            return True
        
        try:
            # Find the actor by name
            actor = self._find_actor_by_name(occlusion.name)
            if not actor:
                self._log_warning(f"Actor not found: {occlusion.name}")
                return False
            
            # Set visibility
            if visible:
                actor.set_is_temporarily_hidden_in_editor(False)
            else:
                actor.set_is_temporarily_hidden_in_editor(True)
            
            # Update occlusion object
            occlusion.is_visible = visible
            
            return True
            
        except Exception as e:
            self._log_warning(f"Failed to set visibility for {occlusion.name}: {e}")
            return False
    
    async def wait_for_scene_stabilization(self) -> None:
        """
        Wait for scene stabilization after visibility changes
        """
        await asyncio.sleep(self._scene_stabilization_delay)
        
        # Additional checks could be added here:
        # - Check if rendering is complete
        # - Verify visibility states
        # - Wait for physics to settle
    
    def _find_actor_by_name(self, actor_name: str) -> Optional:
        """
        Find an actor by its label name
        
        Args:
            actor_name: Name/label of the actor to find
            
        Returns:
            Actor object or None if not found
        """
        if not unreal:
            return None
        
        try:
            editor_actor_subsystem = unreal.get_editor_subsystem(unreal.EditorActorSubsystem)
            all_actors = editor_actor_subsystem.get_all_level_actors()
            
            for actor in all_actors:
                if actor.get_actor_label() == actor_name:
                    return actor
            
            return None
            
        except Exception as e:
            self._log_error(f"Failed to find actor {actor_name}: {e}")
            return None
    
    async def restore_original_state(self) -> bool:
        """
        Restore all occlusion objects to their original visibility states
        
        Returns:
            True if successful, False otherwise
        """
        try:
            self._log_info("Restoring original occlusion visibility states")
            success_count = 0
            
            for occlusion in self.discovered_occlusions:
                original_state = self.original_visibility_states.get(occlusion.name, True)
                if await self._set_single_occlusion_visibility(occlusion, original_state):
                    success_count += 1
            
            # Wait for scene stabilization
            await self.wait_for_scene_stabilization()
            
            if success_count == len(self.discovered_occlusions):
                self._log_info("Successfully restored original occlusion states")
                return True
            else:
                self._log_warning(f"Partial success in restoring occlusion states: {success_count}/{len(self.discovered_occlusions)}")
                return False
                
        except Exception as e:
            self._log_error(f"Failed to restore original occlusion states: {e}")
            return False
    
    def get_occlusion_by_name(self, occlusion_name: str) -> Optional[OcclusionObject]:
        """
        Get OcclusionObject by name
        
        Args:
            occlusion_name: Name of the occlusion object to find
            
        Returns:
            OcclusionObject or None if not found
        """
        for occlusion in self.discovered_occlusions:
            if occlusion.name == occlusion_name:
                return occlusion
        return None
    
    def get_visible_occlusions(self) -> List[OcclusionObject]:
        """
        Get list of currently visible occlusion objects
        
        Returns:
            List of visible OcclusionObject instances
        """
        return [occlusion for occlusion in self.discovered_occlusions if occlusion.is_visible]
    
    def get_hidden_occlusions(self) -> List[OcclusionObject]:
        """
        Get list of currently hidden occlusion objects
        
        Returns:
            List of hidden OcclusionObject instances
        """
        return [occlusion for occlusion in self.discovered_occlusions if not occlusion.is_visible]
    
    def cleanup(self) -> None:
        """Cleanup Occlusion Manager resources"""
        # Restore original states if needed
        if unreal and self.discovered_occlusions:
            try:
                asyncio.create_task(self.restore_original_state())
            except Exception as e:
                self._log_warning(f"Failed to restore occlusion states during cleanup: {e}")
        
        self.discovered_occlusions.clear()
        self.original_visibility_states.clear()
        self.current_occlusion_state = True
        self._retry_counts.clear()
        self._log_info("Occlusion Manager cleaned up")
    
    def get_status(self) -> Dict[str, Any]:
        """
        Get current status of the Occlusion Manager
        
        Returns:
            Dictionary with detailed status information
        """
        base_status = super().get_status()
        
        occlusion_status = {
            "occlusion_name_pattern": self.occlusion_name_pattern,
            "total_occlusions_discovered": len(self.discovered_occlusions),
            "visible_occlusions": len(self.get_visible_occlusions()),
            "hidden_occlusions": len(self.get_hidden_occlusions()),
            "current_mode": "OCC" if self.current_occlusion_state else "GT",
            "scene_stabilization_delay": self._scene_stabilization_delay,
            "occlusion_summary": {
                "static_mesh": len([o for o in self.discovered_occlusions if o.object_type == "static_mesh"]),
                "skeletal_mesh": len([o for o in self.discovered_occlusions if o.object_type == "skeletal_mesh"]),
            }
        }
        
        base_status.update(occlusion_status)
        return base_status


# Utility functions
def create_occlusion_manager_from_config(pipeline_config) -> OcclusionManager:
    """
    Create an OcclusionManager instance from pipeline configuration
    
    Args:
        pipeline_config: PipelineConfig object
        
    Returns:
        Configured OcclusionManager instance
    """
    occlusion_manager = OcclusionManager()
    occlusion_manager.set_max_retries(pipeline_config.max_retry_attempts)
    
    success = occlusion_manager.initialize(
        occlusion_name_pattern=pipeline_config.occlusion_name_pattern,
        scene_stabilization_delay=pipeline_config.scene_stabilization_delay
    )
    
    if not success:
        raise RuntimeError("Failed to initialize Occlusion Manager")
    
    return occlusion_manager