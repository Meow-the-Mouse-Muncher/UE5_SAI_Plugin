# 配置文件示例

本目录包含Pipeline Automation System的配置文件示例和模板。

## 文件结构

- `pipeline_config.json` - 主管道配置文件
- `trajectories.json` - 轨迹配置文件
- `map_configs/` - 地图特定配置文件
- `templates/` - 配置模板文件

## 配置文件说明

### 1. 管道配置 (pipeline_config.json)

主要配置文件，包含系统运行的基本参数：

```json
{
  "maps_directory": "Content/Map",
  "trajectory_file_path": "config/trajectories.json", 
  "height_variants": [0.0, 500.0, 1000.0],
  "output_directory": "output/pipeline_data",
  "max_retry_attempts": 3,
  "scene_stabilization_delay": 2.0
}
```

### 2. 轨迹配置 (trajectories.json)

定义相机轨迹的配置文件，支持多种轨迹类型：

#### Box轨迹
围绕目标的矩形轨迹，适用于全方位数据采集。

#### Line轨迹  
直线轨迹，适用于特定角度的数据采集。

#### Circular轨迹
圆形轨迹，适用于360度环绕数据采集。

### 3. 地图配置

每个地图可以有独立的配置文件，包含：
- 放置边界
- 排除区域
- 推荐轨迹
- 特殊设置

## 使用方法

1. 复制模板文件到项目目录
2. 根据需要修改配置参数
3. 使用配置验证工具检查配置正确性
4. 运行管道系统

## 配置验证

使用内置验证器检查配置文件：

```python
from pipeline_automation.config_templates import validate_config

if validate_config(Path("pipeline_config.json")):
    print("配置验证通过")
else:
    print("配置验证失败")
```