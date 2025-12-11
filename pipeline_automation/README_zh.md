# 流水线自动化系统

一个自动化的多地图数据采集流水线系统，扩展了现有的 `MatrixCityPlugin`，用于在多个地图、多个目标和多条轨迹上系统化采样。

## 概述

流水线自动化系统负责在多个 Unreal Engine 地图间编排自动采样，系统化地处理目标对象、可配置的多高度轨迹，并生成用于计算机视觉的被遮挡（OCC）与真值（GT）数据集。

## 功能特性

- **多地图处理**：自动发现并按顺序处理多个地图
- **目标管理**：发现并管理 `Target_XXX` 对象，带可见性控制
- **遮挡控制**：管理 `SM_*` 遮挡对象以生成 OCC/GT 数据
- **轨迹系统**：支持箱形、直线、圆形与自定义轨迹模式
- **高度变体**：在多个高度层执行轨迹采样
- **进度跟踪**：包含时间估算的全面进度监控
- **错误处理**：可配置重试机制的健壮错误恢复
- **状态持久化**：保存与恢复流水线执行状态
- **详尽日志**：带性能指标的结构化日志

## 架构

### 核心组件

1. **Models**（`models.py`）：核心数据结构与类型定义
2. **Configuration**（`config.py`）：配置管理与验证
3. **Progress Tracking**（`progress.py`）：进度监控与日志
4. **Managers**（`managers/`）：流水线操作的组件管理器
   - `MapManager`：地图发现、加载与验证
   - `BaseManager`：各管理器的基类

### 关键数据模型

- `PipelineConfig`：主流水线配置
- `MapInfo`：地图信息与处理状态
- `TargetObject`：目标对象表征
- `TrajectoryDefinition`：相机轨迹定义
- `ProgressStatus`：实时进度追踪
- `SamplingSession`：单次采样会话数据

## 快速上手

### 1. 创建配置

```python
from pipeline_automation import create_sample_config
from pathlib import Path

# 创建示例配置文件
config_dir = Path("./config")
create_sample_config(config_dir)
```

### 2. 加载配置

```python
from pipeline_automation import ConfigManager

config_manager = ConfigManager(config_dir)
pipeline_config = config_manager.load_pipeline_config(
    config_dir / "pipeline_config.json"
)
```

### 3. 初始化进度跟踪

```python
from pipeline_automation import ProgressTracker, PipelineLogger

# 设置日志
logger = PipelineLogger(Path("./logs"))

# 初始化进度追踪器
progress_tracker = ProgressTracker(
    state_file=Path("./progress_state.json")
)

# 初始化流水线状态（示例）
progress_tracker.initialize_pipeline(
    total_maps=5,
    total_targets=25,
    total_sessions=75
)
```

### 4. 使用 Map Manager

```python
from pipeline_automation import MapManager, create_map_manager_from_config

# 创建 Map Manager
map_manager = create_map_manager_from_config(config_manager, pipeline_config)

# 发现并校验地图
discovered_maps = map_manager.discover_maps()
valid_maps = map_manager.get_valid_maps()

# 加载指定地图（示例，需在 async 环境中运行）
import asyncio
success = await map_manager.load_map(valid_maps[0])

# 使用回调处理所有地图
async def process_map(map_info):
    print(f"Processing {map_info.name}")
    return True  # 继续处理

successful, total = await map_manager.process_all_maps(callback=process_map)
```

### 5. 执行流水线处理（示例）

```python
# 开始地图处理
progress_tracker.start_map_processing("map_001", target_count=5)

# 开始目标处理
progress_tracker.start_target_processing("Target_001")

# 启动采样会话
progress_tracker.start_session("map_001", "Target_001", 500.0, "box_trajectory")

# 完成会话并记录
from pipeline_automation.models import SamplingSession
session = SamplingSession(
    map_name="map_001",
    target_name="Target_001", 
    height_offset=500.0,
    trajectory_name="box_trajectory"
)
progress_tracker.complete_session(session, success=True)
```

## 配置文件说明

### 流水线配置（`pipeline_config.json`）

（示例 JSON 内容与英文版本相同）

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

### 轨迹定义（`trajectory.json`）

（示例 JSON 内容与英文版本相同）

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

### 放置配置（`placement_map_name.json`）

（示例 JSON 内容与英文版本相同）

```json
{
  "min_bounds": [-50000.0, -50000.0, 0.0],
  "max_bounds": [50000.0, 50000.0, 5000.0],
  "default_height": 300.0,
  "exclusion_zones": []
}
```

## 与现有插件的集成

流水线自动化系统设计为扩展现有的 `MatrixCityPlugin`：

1. **复用现有工具**：利用 `utils_sequencer.py`、`utils_actor.py` 等现有工具函数
2. **扩展数据模型**：在现有 `SequenceKey` 等模型基础上扩展
3. **集成 UE 进程管理**：与现有的 `run_cmd_async.py` 协同工作
4. **保证兼容性**：保留插件的现有功能以便渐进集成

## 错误处理

系统包含完善的错误处理策略：

- 地图加载错误：跳过地图并继续处理
- 目标发现错误：按配置重试次数进行重试
- 轨迹执行错误：在重置场景后重试
- UE 进程崩溃：自动重启并尝试恢复
- FBX 导出错误：不阻塞流水线，记录失败

## 性能监控

内置性能跟踪项：

- 每小时完成会话数
- 平均会话时间
- 错误与重试统计
- 进度百分比与时间估算
- 资源利用率

## 状态持久化

系统支持保存与恢复执行状态：

- 进度计数器
- 当前处理位置
- 性能指标
- 错误历史
- 会话时间数据

## 示例用法

参见 `examples/basic_usage.py`，其中演示了：

- 配置创建与加载
- 进度追踪初始化
- 模拟流水线执行
- 报告生成

## 要求

- Python 3.8+
- Unreal Engine 5.x
- 已安装的 `MatrixCityPlugin`
- 所需 Python 包：`pydantic`, `pyyaml`

## 目录结构

```
pipeline_automation/
├── __init__.py          # 主模块导出
├── models.py            # 核心数据模型
├── config.py            # 配置管理
├── progress.py          # 进度跟踪与日志
├── examples/            # 使用示例
│   └── basic_usage.py   # 基本使用演示
└── README.md            # 原始英文文档
```

## 实现状态

### ✅ 已完成组件

1. **核心数据模型**：完整的数据结构与校验
2. **配置管理**：JSON/YAML 配置加载与校验
3. **进度追踪**：全面的进度监控与日志
4. **地图管理器**：地图发现、加载与验证

### 🚧 待实现步骤

1. 目标管理器组件（`Target_XXX` 对象管理）
2. 遮挡管理器组件（`SM_*` 对象可见性控制）
3. 轨迹管理器组件（轨迹执行与 FBX 导出）
4. UE 进程管理扩展（崩溃恢复与通信）
5. Pipeline Controller 编排（主执行循环）
6. 集成测试与性能优化

## 许可证

该模块为 Pipeline Automation System 项目的一部分。
