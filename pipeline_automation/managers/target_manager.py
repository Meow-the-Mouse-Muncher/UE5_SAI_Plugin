"""
Target Manager component for the Pipeline Automation System.

This module handles Target_XXX object discovery, positioning, and visibility control.
It manages target objects that are pre-placed in maps and provides functionality
for systematic processing of each target.
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
    from ..models import TargetObject, ProcessingStatus
    from .base_manager import BaseManager
except ImportError:
    import sys
    from pathlib import Path
    sys.path.append(str(Path(__file__).parent.parent))
    from models import TargetObject, ProcessingStatus
    from base_manager import BaseManager


class TargetManager(BaseManager):
    """
    Manages Target_XXX object discovery and visibility control.
    
    This manager handles:
    - Discovery of Target_XXX objects in the current scene
    - Visibility control (show only one target at a time)
    - Target positioning and validation
    - Error handling and retry logic for target operations
    """
    
    def __init__(self, **kwargs):
        """
        Initialize Target Manager
        
        Args:
            **kwargs: Additional arguments passed to BaseManager
        """
        super().__init__(**kwargs)
        self.target_name_pattern = "Target_*"
        self.discovered_targets: List[TargetObject] = []
        self.current_target: Optional[TargetObject] = None
        self.original_visibility_states: Dict[str, bool] = {}
        
        # Target discovery settings
        self._discovery_timeout = 30.0  # seconds
        self._visibility_change_delay = 0.5  # seconds
    
    def initialize(self, target_name_pattern: str = "Target_*") -> bool:
        """
        Initialize the Target Manager
        
        Args:
            target_name_pattern: Pattern for target object names (e.g., "Target_*")
            
        Returns:
            True if initialization successful, False otherwise
        """
        try:
            self.target_name_pattern = target_name_pattern
            self._log_info(f"Target Manager initialized with pattern: {target_name_pattern}")
            return True
            
        except Exception as e:
            self._log_error(f"Failed to initialize Target Manager: {e}")
            return False
    
    async def discover_targets(self) -> List[TargetObject]:
        """
        Discover all Target_XXX objects in the current scene
        
        Returns:
            List of discovered TargetObject instances
        """
        if not unreal:
            # Simulate discovery for testing
            self._log_warning("Unreal Engine not available, simulating target discovery")
            mock_targets = [
                TargetObject(name="Target_001", position=(1000.0, 2000.0, 300.0)),
                TargetObject(name="Target_002", position=(3000.0, 4000.0, 300.0)),
                TargetObject(name="Target_003", position=(5000.0, 6000.0, 300.0))
            ]
            self.discovered_targets = mock_targets
            return mock_targets
        
        operation_key = "discover_targets"
        
        while self._should_retry(operation_key):
            try:
                attempt = self._increment_retry_count(operation_key)
                
                if attempt > 1:
                    self._log_retry("target discovery", attempt, "Previous attempt failed")
                
                self.discovered_targets.clear()
                
                # Get all actors in the scene
                editor_actor_subsystem = unreal.get_editor_subsystem(unreal.EditorActorSubsystem)
                all_actors = editor_actor_subsystem.get_all_level_actors()
                
                if not all_actors:
                    self._log_warning("No actors found in the scene")
                    return []
                
                # Filter actors by name pattern
                target_actors = []
                for actor in all_actors:
                    actor_label = actor.get_actor_label()
                    if fnmatch.fnmatch(actor_label, self.target_name_pattern):
                        target_actors.append(actor)
                
                if not target_actors:
                    self._log_warning(f"No targets found matching pattern: {self.target_name_pattern}")
                    return []
                
                # Sort targets by name for consistent processing order
                target_actors.sort(key=lambda actor: actor.get_actor_label())
                
                # Create TargetObject instances
                for actor in target_actors:
                    try:
                        actor_label = actor.get_actor_label()
                        location = actor.get_actor_location()
                        position = (location.x, location.y, location.z)
                        
                        # Check if actor is currently visible
                        is_visible = not actor.is_hidden_ed()
                        
                        target_obj = TargetObject(
                            name=actor_label,
                            position=position,
                            is_visible=is_visible,
                            processing_status=ProcessingStatus.NOT_STARTED
                        )
                        
                        self.discovered_targets.append(target_obj)
                        
                        # Store original visibility state
                        self.original_visibility_states[actor_label] = is_visible
                        
                    except Exception as e:
                        self._log_warning(f"Failed to process target actor {actor.get_actor_label()}: {e}")
                        continue
                
                self._reset_retry_count(operation_key)
                self._log_info(f"Discovered {len(self.discovered_targets)} targets: {[t.name for t in self.discovered_targets]}")
                
                return self.discovered_targets
                
            except Exception as e:
                error_msg = f"Failed to discover targets: {e}"
                
                if not self._should_retry(operation_key):
                    self._log_error(error_msg)
                    return []
                else:
                    self._log_warning(error_msg)
                    await asyncio.sleep(1)  # Brief delay before retry
        
        return []
    
    async def set_target_visibility(self, target: TargetObject, visible: bool) -> bool:
        """
        Set visibility of a specific target
        
        Args:
            target: TargetObject to modify
            visible: True to show, False to hide
            
        Returns:
            True if successful, False otherwise
        """
        if not unreal:
            # Simulate visibility change for testing
            target.is_visible = visible
            self._log_info(f"Simulated visibility change for {target.name}: {visible}")
            return True
        
        operation_key = f"set_visibility_{target.name}"
        
        while self._should_retry(operation_key):
            try:
                attempt = self._increment_retry_count(operation_key)
                
                if attempt > 1:
                    self._log_retry(f"setting visibility for {target.name}", attempt, "Previous attempt failed")
                
                # Find the actor by name
                actor = self._find_actor_by_name(target.name)
                if not actor:
                    raise RuntimeError(f"Actor not found: {target.name}")
                
                # Set visibility
                if visible:
                    actor.set_is_temporarily_hidden_in_editor(False)
                else:
                    actor.set_is_temporarily_hidden_in_editor(True)
                
                # Wait for visibility change to take effect
                await asyncio.sleep(self._visibility_change_delay)
                
                # Verify the change
                actual_visibility = not actor.is_hidden_ed()
                if actual_visibility != visible:
                    raise RuntimeError(f"Visibility change verification failed for {target.name}")
                
                # Update target object
                target.is_visible = visible
                
                self._reset_retry_count(operation_key)
                self._log_info(f"Set visibility for {target.name}: {visible}")
                
                return True
                
            except Exception as e:
                error_msg = f"Failed to set visibility for {target.name}: {e}"
                
                if not self._should_retry(operation_key):
                    self._log_error(error_msg)
                    return False
                else:
                    self._log_warning(error_msg)
                    await asyncio.sleep(0.5)  # Brief delay before retry
        
        return False
    
    async def hide_all_targets_except(self, active_target: TargetObject) -> bool:
        """
        Hide all targets except the specified active target
        
        Args:
            active_target: TargetObject to keep visible
            
        Returns:
            True if successful, False otherwise
        """
        try:
            success_count = 0
            total_targets = len(self.discovered_targets)
            
            for target in self.discovered_targets:
                if target.name == active_target.name:
                    # Ensure active target is visible
                    if await self.set_target_visibility(target, True):
                        success_count += 1
                else:
                    # Hide other targets
                    if await self.set_target_visibility(target, False):
                        success_count += 1
            
            # Update current target
            self.current_target = active_target
            
            if success_count == total_targets:
                self._log_info(f"Successfully configured visibility: {active_target.name} visible, {total_targets - 1} hidden")
                return True
            else:
                self._log_warning(f"Partial success in visibility configuration: {success_count}/{total_targets}")
                return False
                
        except Exception as e:
            self._log_error(f"Failed to configure target visibility: {e}")
            return False
    
    def get_target_position(self, target: TargetObject) -> Tuple[float, float, float]:
        """
        Get the current position of a target object
        
        Args:
            target: TargetObject to query
            
        Returns:
            Position tuple (x, y, z)
        """
        if not unreal:
            return target.position
        
        try:
            actor = self._find_actor_by_name(target.name)
            if actor:
                location = actor.get_actor_location()
                position = (location.x, location.y, location.z)
                # Update cached position
                target.position = position
                return position
            else:
                self._log_warning(f"Actor not found for position query: {target.name}")
                return target.position
                
        except Exception as e:
            self._log_error(f"Failed to get position for {target.name}: {e}")
            return target.position
    
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
    
    def get_target_by_name(self, target_name: str) -> Optional[TargetObject]:
        """
        Get TargetObject by name
        
        Args:
            target_name: Name of the target to find
            
        Returns:
            TargetObject or None if not found
        """
        for target in self.discovered_targets:
            if target.name == target_name:
                return target
        return None
    
    def get_valid_targets(self) -> List[TargetObject]:
        """
        Get list of targets that are ready for processing
        
        Returns:
            List of valid TargetObject instances
        """
        return [target for target in self.discovered_targets 
                if target.processing_status != ProcessingStatus.FAILED]
    
    async def restore_original_visibility(self) -> bool:
        """
        Restore all targets to their original visibility states
        
        Returns:
            True if successful, False otherwise
        """
        try:
            success_count = 0
            
            for target in self.discovered_targets:
                original_state = self.original_visibility_states.get(target.name, True)
                if await self.set_target_visibility(target, original_state):
                    success_count += 1
            
            self.current_target = None
            
            if success_count == len(self.discovered_targets):
                self._log_info("Successfully restored original visibility states")
                return True
            else:
                self._log_warning(f"Partial success in restoring visibility: {success_count}/{len(self.discovered_targets)}")
                return False
                
        except Exception as e:
            self._log_error(f"Failed to restore original visibility: {e}")
            return False
    
    async def process_all_targets(self, callback=None) -> Tuple[int, int]:
        """
        Process all discovered targets in sequence
        
        Args:
            callback: Optional callback function called for each target
                     Signature: callback(target: TargetObject) -> bool
                     Return False to skip remaining targets
            
        Returns:
            Tuple of (successful_targets, total_targets)
        """
        if not self.discovered_targets:
            await self.discover_targets()
        
        successful_targets = 0
        total_targets = len(self.discovered_targets)
        
        for target in self.discovered_targets:
            try:
                # Set this target as active (hide others)
                if await self.hide_all_targets_except(target):
                    target.processing_status = ProcessingStatus.IN_PROGRESS
                    
                    # Call callback if provided
                    if callback:
                        try:
                            should_continue = callback(target)
                            if should_continue is False:
                                self._log_info("Processing stopped by callback")
                                break
                        except Exception as e:
                            self._log_error(f"Callback error for target {target.name}: {e}")
                            target.processing_status = ProcessingStatus.FAILED
                            continue
                    
                    target.processing_status = ProcessingStatus.COMPLETED
                    successful_targets += 1
                else:
                    self._log_error(f"Failed to configure visibility for target: {target.name}")
                    target.processing_status = ProcessingStatus.FAILED
                    
            except Exception as e:
                self._log_error(f"Error processing target {target.name}: {e}")
                target.processing_status = ProcessingStatus.FAILED
        
        # Restore original visibility
        await self.restore_original_visibility()
        
        return successful_targets, total_targets
    
    def cleanup(self) -> None:
        """Cleanup Target Manager resources"""
        # Restore visibility if needed
        if unreal and self.discovered_targets:
            try:
                asyncio.create_task(self.restore_original_visibility())
            except Exception as e:
                self._log_warning(f"Failed to restore visibility during cleanup: {e}")
        
        self.discovered_targets.clear()
        self.original_visibility_states.clear()
        self.current_target = None
        self._retry_counts.clear()
        self._log_info("Target Manager cleaned up")
    
    def get_status(self) -> Dict[str, Any]:
        """
        Get current status of the Target Manager
        
        Returns:
            Dictionary with detailed status information
        """
        base_status = super().get_status()
        
        target_status = {
            "target_name_pattern": self.target_name_pattern,
            "total_targets_discovered": len(self.discovered_targets),
            "valid_targets": len(self.get_valid_targets()),
            "current_target": self.current_target.name if self.current_target else None,
            "target_details": [
                {
                    "name": target.name,
                    "position": target.position,
                    "is_visible": target.is_visible,
                    "status": target.processing_status.value,
                    "error": target.error_message
                }
                for target in self.discovered_targets
            ]
        }
        
        base_status.update(target_status)
        return base_status


# Utility functions
def create_target_manager_from_config(pipeline_config) -> TargetManager:
    """
    Create a TargetManager instance from pipeline configuration
    
    Args:
        pipeline_config: PipelineConfig object
        
    Returns:
        Configured TargetManager instance
    """
    target_manager = TargetManager()
    target_manager.set_max_retries(pipeline_config.max_retry_attempts)
    
    success = target_manager.initialize(
        target_name_pattern=pipeline_config.target_name_pattern
    )
    
    if not success:
        raise RuntimeError("Failed to initialize Target Manager")
    
    return target_manager