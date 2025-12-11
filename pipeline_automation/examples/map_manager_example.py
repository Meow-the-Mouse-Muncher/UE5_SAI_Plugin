"""
Map Manager usage example for the Pipeline Automation System.

This example demonstrates how to use the MapManager to discover, validate,
and load maps in the pipeline.
"""

import asyncio
import logging
from pathlib import Path

# Setup path for imports
import sys
sys.path.append(str(Path(__file__).parent.parent))

try:
    from pipeline_automation import (
        ConfigManager, MapManager, ProgressTracker, PipelineLogger,
        create_default_pipeline_config
    )
except ImportError:
    from config import ConfigManager
    from managers.map_manager import MapManager
    from progress import ProgressTracker, PipelineLogger
    from models import create_default_pipeline_config


async def demonstrate_map_manager():
    """Demonstrate Map Manager functionality"""
    
    # Setup example directory
    example_dir = Path("./map_manager_example")
    example_dir.mkdir(exist_ok=True)
    
    # Create mock maps directory with some .umap files
    maps_dir = example_dir / "Content" / "Map"
    maps_dir.mkdir(parents=True, exist_ok=True)
    
    # Create some mock map files
    mock_maps = ["main.umap", "test_level.umap", "demo_scene.umap"]
    for map_name in mock_maps:
        map_file = maps_dir / map_name
        map_file.write_text("Mock UE map file content")
    
    # Initialize logging
    logger = PipelineLogger(example_dir / "logs")
    
    # Create configuration manager
    config_manager = ConfigManager(example_dir / "config")
    
    # Create sample configuration
    pipeline_config = create_default_pipeline_config(
        maps_dir=maps_dir,
        trajectory_file=example_dir / "config" / "trajectory.json",
        output_dir=example_dir / "output"
    )
    
    # Initialize progress tracker
    progress_tracker = ProgressTracker(
        state_file=example_dir / "progress_state.json"
    )
    
    # Create Map Manager
    map_manager = MapManager(
        config_manager=config_manager,
        progress_tracker=progress_tracker
    )
    
    # Initialize Map Manager
    success = map_manager.initialize(
        maps_directory=maps_dir,
        scene_stabilization_delay=1.0  # Shorter delay for demo
    )
    
    if not success:
        logging.error("Failed to initialize Map Manager")
        return
    
    logging.info("Map Manager initialized successfully")
    
    # Discover maps
    discovered_maps = map_manager.discover_maps()
    logging.info(f"Discovered {len(discovered_maps)} maps")
    
    for map_info in discovered_maps:
        logging.info(f"  - {map_info.name}: {map_info.path}")
    
    # Validate maps
    valid_maps = []
    for map_info in discovered_maps:
        if map_manager.validate_map(map_info):
            valid_maps.append(map_info)
            logging.info(f"✓ Map {map_info.name} is valid")
        else:
            logging.warning(f"✗ Map {map_info.name} is invalid: {map_info.error_message}")
    
    logging.info(f"Found {len(valid_maps)} valid maps")
    
    # Process maps with callback
    def map_callback(map_info):
        logging.info(f"Processing callback for map: {map_info.name}")
        # Simulate some processing
        import time
        time.sleep(0.1)
        return True  # Continue processing
    
    successful, total = await map_manager.process_all_maps(callback=map_callback)
    logging.info(f"Processed {successful}/{total} maps successfully")
    
    # Get detailed status
    status = map_manager.get_status()
    logging.info("Map Manager Status:")
    logging.info(f"  Total maps discovered: {status['total_maps_discovered']}")
    logging.info(f"  Valid maps: {status['valid_maps']}")
    logging.info(f"  Current map: {status['current_map']}")
    
    # Show map details
    for map_detail in status['map_details']:
        logging.info(f"  Map {map_detail['name']}: {map_detail['status']}")
        if map_detail['error']:
            logging.info(f"    Error: {map_detail['error']}")
    
    # Cleanup
    map_manager.cleanup()
    logging.info("Map Manager demonstration completed")
    
    # Cleanup example directory
    import shutil
    shutil.rmtree(example_dir)
    logging.info(f"Cleaned up example directory: {example_dir}")


async def demonstrate_error_handling():
    """Demonstrate Map Manager error handling"""
    
    logging.info("=" * 50)
    logging.info("DEMONSTRATING ERROR HANDLING")
    logging.info("=" * 50)
    
    # Setup with invalid directory
    config_manager = ConfigManager()
    map_manager = MapManager(config_manager)
    
    # Try to initialize with non-existent directory
    success = map_manager.initialize(Path("/non/existent/directory"))
    if not success:
        logging.info("✓ Correctly handled non-existent directory")
    
    # Try to initialize with a file instead of directory
    temp_file = Path("temp_file.txt")
    temp_file.write_text("not a directory")
    
    success = map_manager.initialize(temp_file)
    if not success:
        logging.info("✓ Correctly handled file instead of directory")
    
    temp_file.unlink()  # Clean up
    
    # Test discovery with empty directory
    empty_dir = Path("empty_maps")
    empty_dir.mkdir(exist_ok=True)
    
    map_manager.initialize(empty_dir)
    maps = map_manager.discover_maps()
    
    if len(maps) == 0:
        logging.info("✓ Correctly handled empty maps directory")
    
    empty_dir.rmdir()  # Clean up


async def main():
    """Run all demonstrations"""
    logging.info("Starting Map Manager demonstrations...")
    
    await demonstrate_map_manager()
    await demonstrate_error_handling()
    
    logging.info("All demonstrations completed successfully!")


if __name__ == "__main__":
    # Setup logging
    logging.basicConfig(
        level=logging.INFO,
        format='[%(asctime)s] %(levelname)-8s %(message)s',
        datefmt='%H:%M:%S'
    )
    
    # Run demonstrations
    asyncio.run(main())