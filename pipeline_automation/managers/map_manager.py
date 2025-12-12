"""
Map Manager component for the Pipeline Automation System.

This module handles map discovery, loading, validation, and switching operations.
It integrates with the existing MatrixCityPlugin utilities and provides robust
error handling for map-related operations.
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
    from ..models import MapInfo, PlacementConfig, ProcessingStatus
    from ..config import ConfigManager
    from .base_manager import BaseManager
except ImportError:
    import sys
    from pathlib import Path
    sys.path.append(str(Path(__file__).parent.parent))
    from models import MapInfo, PlacementConfig, ProcessingStatus
    from config import ConfigManager
    from base_manager import BaseManager


class MapManager(BaseManager):
    """
    Manages map discovery, loading, and validation operations.
    
    This manager handles:
    - Automatic map discovery from Content/Map directory
    - Map loading and validation
    - Map-specific placement configuration loading
    - Error handling and retry logic for map operations
    """
    
    def __init__(self, config_manager: ConfigManager, **kwargs):
        """
        Initialize Map Manager
        
        Args:
            config_manager: Configuration manager instance
            **kwargs: Additional arguments passed to BaseManager
        """
        super().__init__(**kwargs)
        self.config_manager = config_manager
        self.maps_directory: Optional[Path] = None
        self.discovered_maps: List[MapInfo] = []
        self.current_map: Optional[MapInfo] = None
        self.placement_configs: Dict[str, PlacementConfig] = {}
        
        # Map loading state
        self._map_loading_timeout = 60.0  # seconds
        self._scene_stabilization_delay = 2.0  # seconds
    
    def initialize(self, maps_directory: Path, scene_stabilization_delay: float = 2.0) -> bool:
        """
        Initialize the Map Manager
        
        Args:
            maps_directory: Directory containing map files
            scene_stabilization_delay: Time to wait for scene stabilization
            
        Returns:
            True if initialization successful, False otherwise
        """
        try:
            self.maps_directory = Path(maps_directory)
            self._scene_stabilization_delay = scene_stabilization_delay
            
            if not self.maps_directory.exists():
                self._log_error(f"Maps directory does not exist: {self.maps_directory}")
                return False
            
            if not self.maps_directory.is_dir():
                self._log_error(f"Maps directory is not a directory: {self.maps_directory}")
                return False
            
            self._log_info(f"Map Manager initialized with directory: {self.maps_directory}")
            return True
            
        except Exception as e:
            self._log_error(f"Failed to initialize Map Manager: {e}")
            return False
    
    def discover_maps(self) -> List[MapInfo]:
        """
        Discover all maps in the maps directory
        
        Returns:
            List of discovered MapInfo objects
        """
        if not self.maps_directory:
            self._log_error("Map Manager not initialized")
            return []
        
        try:
            self.discovered_maps.clear()
            
            # Find all .umap files in the directory
            map_files = list(self.maps_directory.glob("*.umap"))
            print(f"map is found ")
            
            if not map_files:
                self._log_warning(f"No .umap files found in {self.maps_directory}")
                return []
            
            # Sort maps alphabetically for consistent processing order
            map_files.sort()
            
            for map_file in map_files:
                map_name = map_file.stem
                
                # Convert to Unreal Engine path format
                # Assuming maps are in Content/Map directory
                relative_path = map_file.relative_to(Path.cwd())
                ue_path = self._convert_to_ue_path(relative_path)
                
                map_info = MapInfo(
                    name=map_name,
                    path=map_file,
                    is_valid=True,  # Will be validated later
                    processing_status=ProcessingStatus.NOT_STARTED
                )
                
                # Try to load placement configuration for this map
                try:
                    placement_config = self.config_manager.load_placement_config(map_name)
                    map_info.placement_config = placement_config.__dict__
                    self.placement_configs[map_name] = placement_config
                except Exception as e:
                    self._log_warning(f"Could not load placement config for {map_name}: {e}")
                
                self.discovered_maps.append(map_info)
            
            self._log_info(f"Discovered {len(self.discovered_maps)} maps: {[m.name for m in self.discovered_maps]}")
            return self.discovered_maps
            
        except Exception as e:
            self._log_error(f"Failed to discover maps: {e}")
            return []
    
    def _convert_to_ue_path(self, file_path: Path) -> str:
        """
        Convert file system path to Unreal Engine asset path
        
        Args:
            file_path: File system path to the map
            
        Returns:
            Unreal Engine asset path (e.g., /Game/Map/main)
        """
        # Convert Content/Map/main.umap to /Game/Map/main
        path_parts = file_path.parts
        
        # Find the Content directory
        try:
            content_index = path_parts.index('Content')
            game_path_parts = path_parts[content_index + 1:]
            
            # Remove .umap extension
            if game_path_parts[-1].endswith('.umap'):
                game_path_parts = game_path_parts[:-1] + (game_path_parts[-1][:-5],)
            
            ue_path = '/Game/' + '/'.join(game_path_parts)
            return ue_path
            
        except ValueError:
            # Fallback: assume it's directly in the maps directory
            map_name = file_path.stem
            return f'/Game/Map/{map_name}'
    
    async def load_map(self, map_info: MapInfo) -> bool:
        """
        Load a specific map
        
        Args:
            map_info: MapInfo object for the map to load
            
        Returns:
            True if map loaded successfully, False otherwise
        """
        if not unreal:
            self._log_warning("Unreal Engine not available, simulating map load")
            await asyncio.sleep(1)  # Simulate loading time
            return True
        
        operation_key = f"load_map_{map_info.name}"
        
        while self._should_retry(operation_key):
            try:
                attempt = self._increment_retry_count(operation_key)
                
                if attempt > 1:
                    self._log_retry(f"loading map {map_info.name}", attempt, "Previous attempt failed")
                
                # Update status
                map_info.processing_status = ProcessingStatus.IN_PROGRESS
                
                # Convert to UE path format
                ue_path = self._convert_to_ue_path(map_info.path)
                
                self._log_info(f"Loading map: {map_info.name} (path: {ue_path})")
                
                # Use EditorLoadingAndSavingUtils to load the map
                success = unreal.EditorLoadingAndSavingUtils.load_map(ue_path)
                
                if not success:
                    raise RuntimeError(f"EditorLoadingAndSavingUtils.load_map returned False for {ue_path}")
                
                # Wait for scene stabilization
                self._log_info(f"Map loaded, waiting {self._scene_stabilization_delay}s for scene stabilization...")
                await asyncio.sleep(self._scene_stabilization_delay)
                
                # Verify the map is actually loaded
                if not self._verify_map_loaded(ue_path):
                    raise RuntimeError(f"Map verification failed for {ue_path}")
                
                # Update status
                map_info.processing_status = ProcessingStatus.COMPLETED
                map_info.error_message = None
                self.current_map = map_info
                
                self._reset_retry_count(operation_key)
                self._log_info(f"Successfully loaded map: {map_info.name}")
                
                return True
                
            except Exception as e:
                error_msg = f"Failed to load map {map_info.name}: {e}"
                
                if not self._should_retry(operation_key):
                    # Final failure
                    map_info.processing_status = ProcessingStatus.FAILED
                    map_info.error_message = error_msg
                    self._log_error(error_msg)
                    return False
                else:
                    # Will retry
                    self._log_warning(error_msg)
                    await asyncio.sleep(1)  # Brief delay before retry
        
        return False
    
    def _verify_map_loaded(self, ue_path: str) -> bool:
        """
        Verify that a map is actually loaded
        
        Args:
            ue_path: Unreal Engine path to the map
            
        Returns:
            True if map is loaded, False otherwise
        """
        if not unreal:
            return True  # Assume success in test mode
        
        try:
            # Get current world
            editor_subsystem = unreal.get_editor_subsystem(unreal.UnrealEditorSubsystem)
            current_world = editor_subsystem.get_editor_world()
            
            if not current_world:
                return False
            
            # Check if the world name matches expected map
            world_name = current_world.get_name()
            expected_name = ue_path.split('/')[-1]
            
            # The world name might have additional suffixes, so check if it contains the expected name
            return expected_name in world_name
            
        except Exception as e:
            self._log_warning(f"Map verification failed: {e}")
            return False
    
    def validate_map(self, map_info: MapInfo) -> bool:
        """
        Validate a map for pipeline compatibility
        
        Args:
            map_info: MapInfo object to validate
            
        Returns:
            True if map is valid, False otherwise
        """
        try:
            # Check if map file exists
            if not map_info.path.exists():
                map_info.is_valid = False
                map_info.error_message = f"Map file does not exist: {map_info.path}"
                return False
            
            # Check file size (maps should not be empty)
            if map_info.path.stat().st_size == 0:
                map_info.is_valid = False
                map_info.error_message = f"Map file is empty: {map_info.path}"
                return False
            
            # Additional validation could be added here:
            # - Check for required actors/objects
            # - Validate map bounds
            # - Check for specific components
            
            map_info.is_valid = True
            map_info.error_message = None
            return True
            
        except Exception as e:
            map_info.is_valid = False
            map_info.error_message = f"Map validation failed: {e}"
            self._log_error(f"Failed to validate map {map_info.name}: {e}")
            return False
    
    def get_placement_config(self, map_name: str) -> Optional[PlacementConfig]:
        """
        Get placement configuration for a specific map
        
        Args:
            map_name: Name of the map
            
        Returns:
            PlacementConfig object or None if not found
        """
        return self.placement_configs.get(map_name)
    
    def get_valid_maps(self) -> List[MapInfo]:
        """
        Get list of valid maps that can be processed
        
        Returns:
            List of valid MapInfo objects
        """
        return [map_info for map_info in self.discovered_maps if map_info.is_valid]
    
    def get_map_by_name(self, map_name: str) -> Optional[MapInfo]:
        """
        Get MapInfo by map name
        
        Args:
            map_name: Name of the map to find
            
        Returns:
            MapInfo object or None if not found
        """
        for map_info in self.discovered_maps:
            if map_info.name == map_name:
                return map_info
        return None
    
    async def process_all_maps(self, callback=None) -> Tuple[int, int]:
        """
        Process all discovered maps in sequence
        
        Args:
            callback: Optional callback function called for each map
                     Signature: callback(map_info: MapInfo) -> bool
                     Return False to skip remaining maps
            
        Returns:
            Tuple of (successful_maps, total_maps)
        """
        if not self.discovered_maps:
            self.discover_maps()
        
        successful_maps = 0
        total_maps = len(self.discovered_maps)
        
        for map_info in self.discovered_maps:
            try:
                # Validate map first
                if not self.validate_map(map_info):
                    self._log_warning(f"Skipping invalid map: {map_info.name}")
                    continue
                
                # Load map
                if await self.load_map(map_info):
                    successful_maps += 1
                    
                    # Call callback if provided
                    if callback:
                        try:
                            should_continue = callback(map_info)
                            if should_continue is False:
                                self._log_info("Processing stopped by callback")
                                break
                        except Exception as e:
                            self._log_error(f"Callback error for map {map_info.name}: {e}")
                else:
                    self._log_error(f"Failed to load map: {map_info.name}")
                    
            except Exception as e:
                self._log_error(f"Error processing map {map_info.name}: {e}")
        
        return successful_maps, total_maps
    
    def cleanup(self) -> None:
        """Cleanup Map Manager resources"""
        self.discovered_maps.clear()
        self.placement_configs.clear()
        self.current_map = None
        self._retry_counts.clear()
        self._log_info("Map Manager cleaned up")
    
    def get_status(self) -> Dict[str, Any]:
        """
        Get current status of the Map Manager
        
        Returns:
            Dictionary with detailed status information
        """
        base_status = super().get_status()
        
        map_status = {
            "maps_directory": str(self.maps_directory) if self.maps_directory else None,
            "total_maps_discovered": len(self.discovered_maps),
            "valid_maps": len(self.get_valid_maps()),
            "current_map": self.current_map.name if self.current_map else None,
            "placement_configs_loaded": len(self.placement_configs),
            "map_details": [
                {
                    "name": map_info.name,
                    "is_valid": map_info.is_valid,
                    "status": map_info.processing_status.value,
                    "error": map_info.error_message
                }
                for map_info in self.discovered_maps
            ]
        }
        
        base_status.update(map_status)
        return base_status


# Utility functions for integration with existing plugin
def create_map_manager_from_config(config_manager: ConfigManager, pipeline_config) -> MapManager:
    """
    Create a MapManager instance from pipeline configuration
    
    Args:
        config_manager: ConfigManager instance
        pipeline_config: PipelineConfig object
        
    Returns:
        Configured MapManager instance
    """
    map_manager = MapManager(config_manager)
    map_manager.set_max_retries(pipeline_config.max_retry_attempts)
    
    success = map_manager.initialize(
        maps_directory=pipeline_config.maps_directory,
        scene_stabilization_delay=pipeline_config.scene_stabilization_delay
    )
    
    if not success:
        raise RuntimeError("Failed to initialize Map Manager")
    
    return map_manager