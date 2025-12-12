"""
Basic usage example for the Pipeline Automation System.

This example demonstrates how to create configurations, initialize the system,
and use the core components.
"""

from pathlib import Path
import logging

# Ensure project root is on sys.path when running this example directly.
# Preferred usage is: `python -m pipeline_automation.examples.basic_usage`
import sys
from pathlib import Path as _Path
_project_root = _Path(__file__).resolve().parents[2]
if str(_project_root) not in sys.path:
    sys.path.insert(0, str(_project_root))

from pipeline_automation import (
    PipelineConfig, ConfigManager, ProgressTracker, PipelineLogger,
    create_default_pipeline_config, create_sample_config
)


def main():
    """Demonstrate basic usage of the pipeline automation system"""
    
    # Setup example directory
    example_dir = Path("./pipeline_example")
    example_dir.mkdir(exist_ok=True)
    
    # Initialize logging
    logger = PipelineLogger(example_dir / "logs")
    
    # Create sample configuration files
    config_dir = example_dir / "config"
    # create_sample_config(config_dir)
    
    logging.info("Sample configuration files created")
    
    # Load configuration
    config_manager = ConfigManager(config_dir)
    try:
        pipeline_config = config_manager.load_pipeline_config(
            config_dir / "pipeline_config.json"
        )
        logging.info(f"Pipeline configuration loaded: {pipeline_config.maps_directory}")
        
        # Validate configuration
        errors = config_manager.validate_config_files(pipeline_config)
        if errors:
            logging.error("Configuration validation errors:")
            for error in errors:
                logging.error(f"  - {error}")
        else:
            logging.info("Configuration validation passed")
            
    except Exception as e:
        logging.error(f"Failed to load configuration: {e}")
        return
    
    # Initialize progress tracker
    progress_tracker = ProgressTracker(
        state_file=example_dir / "progress_state.json",
        log_interval=10  # Log every 10 seconds for demo
    )
    
    # Simulate pipeline initialization
    progress_tracker.initialize_pipeline(
        total_maps=3,
        total_targets=15,
        total_sessions=45  # 3 maps * 5 targets * 3 heights
    )
    
    # Simulate some processing
    import time
    
    # Simulate map processing
    for map_idx in range(3):
        map_name = f"map_{map_idx + 1}"
        progress_tracker.start_map_processing(map_name, 5)
        
        # Simulate target processing
        for target_idx in range(5):
            target_name = f"Target_{target_idx + 1:03d}"
            progress_tracker.start_target_processing(target_name)
            
            # Simulate sessions for different heights
            for height in [0.0, 500.0, 1000.0]:
                progress_tracker.start_session(
                    map_name, target_name, height, "box_trajectory"
                )
                
                # Simulate processing time
                time.sleep(0.1)  # Quick simulation
                
                # Create mock session result
                from pipeline_automation.models import SamplingSession, MapInfo, TargetObject
                
                # Create mock objects
                map_info = MapInfo(name=map_name, path=f"/Content/Map/{map_name}")
                target = TargetObject(name=target_name, location=(0, 0, 100))
                
                session = SamplingSession(
                    map_info=map_info,
                    target=target,
                    height_offset=height,
                    session_start_time=time.time() - 0.1,
                    session_end_time=time.time()
                )
                
                progress_tracker.complete_session(session, success=True)
            
            progress_tracker.complete_target_processing(target_name)
        
        progress_tracker.complete_map_processing(map_name)
    
    # Generate final report
    summary_report = progress_tracker.generate_summary_report()
    logging.info("Pipeline simulation completed")
    logger.log_pipeline_complete(summary_report)
    
    # Show detailed status
    detailed_status = progress_tracker.get_detailed_status()
    logging.info(f"Final progress: {detailed_status['overall_progress_percentage']:.1f}%")
    
    print(f"\nExample completed! Check the logs in: {example_dir / 'logs'}")
    print(f"Configuration files created in: {config_dir}")


if __name__ == "__main__":
    main()