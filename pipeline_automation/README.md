# Pipeline Automation System

An automated multi-map data collection pipeline system that extends the existing MatrixCityPlugin to perform systematic sampling across multiple maps, targets, and trajectories.

## Overview

The Pipeline Automation System orchestrates automated sampling across multiple Unreal Engine maps, systematically processing target objects with configurable trajectories at various heights, generating both occluded (OCC) and ground truth (GT) datasets for computer vision applications.

## Features

- **Multi-map Processing**: Automatically discover and process multiple maps in sequence
- **Target Management**: Discover and manage Target_XXX objects with visibility control
- **Occlusion Control**: Manage SM_* occlusion objects for OCC/GT data generation
- **Trajectory System**: Support for box, line, circular, and custom trajectory patterns
- **Height Variants**: Execute trajectories at multiple elevation levels
- **Progress Tracking**: Comprehensive progress monitoring with time estimation
- **Error Handling**: Robust error recovery with configurable retry mechanisms
- **State Persistence**: Save and resume pipeline execution state
- **Comprehensive Logging**: Structured logging with performance metrics

## Architecture

### Core Components

1. **Models** (`models.py`): Core data structures and types
2. **Configuration** (`config.py`): Configuration management and validation
3. **Progress Tracking** (`progress.py`): Progress monitoring and logging
4. **Managers** (`managers/`): Component managers for pipeline operations
   - `MapManager`: Map discovery, loading, and validation
   - `BaseManager`: Base class for all managers

### Key Data Models

- `PipelineConfig`: Main pipeline configuration
- `MapInfo`: Map information and processing status
- `TargetObject`: Target object representation
- `TrajectoryDefinition`: Camera trajectory definition
- `ProgressStatus`: Real-time progress tracking
- `SamplingSession`: Individual sampling session data

## Quick Start

### 1. Create Configuration

```python
from pipeline_automation import create_sample_config
from pathlib import Path

# Create sample configuration files
config_dir = Path("./config")
create_sample_config(config_dir)
```

### 2. Load Configuration

```python
from pipeline_automation import ConfigManager

config_manager = ConfigManager(config_dir)
pipeline_config = config_manager.load_pipeline_config(
    config_dir / "pipeline_config.json"
)
```

### 3. Initialize Progress Tracking

```python
from pipeline_automation import ProgressTracker, PipelineLogger

# Setup logging
logger = PipelineLogger(Path("./logs"))

# Initialize progress tracker
progress_tracker = ProgressTracker(
    state_file=Path("./progress_state.json")
)

# Initialize pipeline
progress_tracker.initialize_pipeline(
    total_maps=5,
    total_targets=25,
    total_sessions=75
)
```

### 4. Use Map Manager

```python
from pipeline_automation import MapManager, create_map_manager_from_config

# Create Map Manager
map_manager = create_map_manager_from_config(config_manager, pipeline_config)

# Discover and validate maps
discovered_maps = map_manager.discover_maps()
valid_maps = map_manager.get_valid_maps()

# Load a specific map
import asyncio
success = await map_manager.load_map(valid_maps[0])

# Process all maps with callback
async def process_map(map_info):
    print(f"Processing {map_info.name}")
    return True  # Continue processing

successful, total = await map_manager.process_all_maps(callback=process_map)
```

### 5. Process Pipeline

```python
# Start map processing
progress_tracker.start_map_processing("map_001", target_count=5)

# Start target processing
progress_tracker.start_target_processing("Target_001")

# Start sampling session
progress_tracker.start_session("map_001", "Target_001", 500.0, "box_trajectory")

# Complete session
from pipeline_automation.models import SamplingSession
session = SamplingSession(
    map_name="map_001",
    target_name="Target_001", 
    height_offset=500.0,
    trajectory_name="box_trajectory"
)
progress_tracker.complete_session(session, success=True)
```

## Configuration Files

### Pipeline Configuration (`pipeline_config.json`)

```json
{
  "maps_directory": "./Content/Map",
  "trajectory_file_path": "./config/trajectory.json",
  "output_directory": "./output",
  "height_variants": [0.0, 500.0, 1000.0, 2000.0],
  "max_retry_attempts": 3,
  "scene_stabilization_delay": 2.0,
  "target_name_pattern": "Target_*",
  "occlusion_name_pattern": "SM_*",
  "sequence_fps": 24.0,
  "default_camera_fov": 90.0
}
```

### Trajectory Definition (`trajectory.json`)

```json
{
  "name": "box_trajectory",
  "trajectory_type": "box",
  "duration": 300.0,
  "keyframes": [
    {
      "time": 0.0,
      "position": [0.0, 0.0, 1000.0],
      "rotation": [0.0, -45.0, 0.0],
      "fov": 90.0
    },
    {
      "time": 100.0,
      "position": [10000.0, 0.0, 1000.0],
      "rotation": [0.0, -45.0, 90.0],
      "fov": 90.0
    }
  ]
}
```

### Placement Configuration (`placement_map_name.json`)

```json
{
  "min_bounds": [-50000.0, -50000.0, 0.0],
  "max_bounds": [50000.0, 50000.0, 5000.0],
  "default_height": 300.0,
  "exclusion_zones": []
}
```

## Integration with Existing Plugin

The Pipeline Automation System is designed to extend the existing MatrixCityPlugin:

1. **Reuses existing utilities**: Leverages `utils_sequencer.py`, `utils_actor.py`, etc.
2. **Extends data models**: Builds upon existing `SequenceKey` and other models
3. **Integrates with UE process management**: Works with existing `run_cmd_async.py`
4. **Maintains compatibility**: Preserves existing plugin functionality

## Error Handling

The system includes comprehensive error handling:

- **Map Loading Errors**: Skip map, continue with next
- **Target Discovery Errors**: Retry up to configured limit
- **Trajectory Execution Errors**: Retry with scene reset
- **UE Process Crashes**: Automatic restart and recovery
- **FBX Export Errors**: Non-blocking, continue pipeline

## Performance Monitoring

Built-in performance tracking includes:

- Sessions per hour
- Average session time
- Error and retry counts
- Progress percentages
- Time estimates
- Resource utilization

## State Persistence

The system can save and restore execution state:

- Progress counters
- Current processing position
- Performance metrics
- Error history
- Session timing data

## Example Usage

See `examples/basic_usage.py` for a complete example demonstrating:

- Configuration creation and loading
- Progress tracking initialization
- Simulated pipeline execution
- Report generation

## Requirements

- Python 3.8+
- Unreal Engine 5.x
- Existing MatrixCityPlugin
- Required Python packages: `pydantic`, `pyyaml`

## Directory Structure

```
pipeline_automation/
├── __init__.py          # Main module exports
├── models.py            # Core data models
├── config.py            # Configuration management
├── progress.py          # Progress tracking and logging
├── examples/            # Usage examples
│   └── basic_usage.py   # Basic usage demonstration
└── README.md           # This file
```

## Implementation Status

### ✅ Completed Components

1. **Core Data Models**: Complete data structure system with validation
2. **Configuration Management**: JSON/YAML config loading with validation  
3. **Progress Tracking**: Comprehensive progress monitoring and logging
4. **Map Manager**: Map discovery, loading, and validation system

### 🚧 Next Implementation Steps

1. Target Manager component (Target_XXX object management)
2. Occlusion Manager component (SM_* object visibility control)
3. Trajectory Manager component (trajectory execution and FBX export)
4. UE Process Manager extensions (crash recovery and communication)
5. Pipeline Controller orchestration (main execution loop)
6. Integration testing and optimization

## License

This module is part of the Pipeline Automation System project.