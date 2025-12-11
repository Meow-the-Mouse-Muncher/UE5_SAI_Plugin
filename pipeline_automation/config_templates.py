"""
配置模板和示例文件生成器

提供默认配置模板和轨迹配置文件格式，支持box、line、circular等模式
"""

import json
import yaml
from pathlib import Path
from typing import Dict, Any, List, Optional
from dataclasses import asdict

from .models import PipelineConfig, TrajectoryConfig


class ConfigTemplates:
    """配置模板生成器"""
    
    @staticmethod
    def create_default_pipeline_config() -> Dict[str, Any]:
        """创建默认管道配置模板"""
        return {
            "maps_directory": "Content/Map",
            "trajectory_file_path": "config/trajectories.json",
            "height_variants": [0.0, 500.0, 1000.0, 1500.0],
            "output_directory": "output/pipeline_data",
            "max_retry_attempts": 3,
            "scene_stabilization_delay": 2.0,
            "target_name_pattern": "Target_*",
            "occlusion_name_pattern": "SM_*",
            "sequence_fps": 24.0,
            "default_camera_fov": 90.0,
            "project_path": "PCGBiomeForestPoplar.uproject",
            "ue_executable_path": "/opt/UnrealEngine/Engine/Binaries/Linux/UnrealEditor",
            "map_filter": [],
            "min_storage_gb": 10.0
        }
    
    @staticmethod
    def create_box_trajectory_template() -> Dict[str, Any]:
        """创建box轨迹配置模板"""
        return {
            "name": "box_trajectory_001",
            "trajectory_type": "box",
            "parameters": {
                "line1": [-5000, -5000, 5000, -5000],  # [x1, y1, x2, y2]
                "line2": [-5000, 5000, 5000, 5000],    # [x1, y1, x2, y2]
                "z": 1000,                              # 高度
                "interval": 800,                        # 采样间隔
                "mode": "train",                        # train/test
                "speed": 500,                           # 移动速度
                "look_at_target": True,                 # 是否朝向目标
                "smooth_transitions": True              # 平滑过渡
            },
            "height_variants": [0, 500, 1000],
            "description": "围绕目标的矩形轨迹，适用于全方位数据采集"
        }
    
    @staticmethod
    def create_line_trajectory_template() -> Dict[str, Any]:
        """创建line轨迹配置模板"""
        return {
            "name": "line_trajectory_001",
            "trajectory_type": "line",
            "parameters": {
                "point1": [-3000, 0],                  # 起点 [x, y]
                "point2": [3000, 0],                   # 终点 [x, y]
                "z": 1200,                             # 高度
                "yaw": 0.0,                            # 基础偏航角
                "mode": "train",                       # train/test
                "speed": 400,                          # 移动速度
                "samples": 20,                         # 采样点数量
                "look_at_target": True                 # 是否朝向目标
            },
            "height_variants": [0, 300, 600],
            "description": "直线轨迹，适用于特定角度的数据采集"
        }
    
    @staticmethod
    def create_circular_trajectory_template() -> Dict[str, Any]:
        """创建circular轨迹配置模板"""
        return {
            "name": "circular_trajectory_001", 
            "trajectory_type": "circular",
            "parameters": {
                "center": [0, 0],                      # 圆心 [x, y]
                "radius": 2000,                        # 半径
                "z": 1500,                             # 高度
                "start_angle": 0,                      # 起始角度
                "end_angle": 360,                      # 结束角度
                "samples": 36,                         # 采样点数量(每10度一个)
                "clockwise": True,                     # 顺时针
                "look_at_center": True,                # 朝向圆心
                "tilt_angle": -15                      # 俯仰角
            },
            "height_variants": [0, 500, 1000],
            "description": "圆形轨迹，适用于360度环绕数据采集"
        }
    
    @staticmethod
    def create_multi_trajectory_config() -> Dict[str, Any]:
        """创建包含多种轨迹的配置文件"""
        return {
            "version": "1.0",
            "description": "多轨迹配置文件示例",
            "default_settings": {
                "sequence_fps": 24.0,
                "default_fov": 90.0,
                "stabilization_frames": 10
            },
            "trajectories": [
                ConfigTemplates.create_box_trajectory_template(),
                ConfigTemplates.create_line_trajectory_template(),
                ConfigTemplates.create_circular_trajectory_template()
            ]
        }
    
    @staticmethod
    def create_map_specific_config(map_name: str) -> Dict[str, Any]:
        """创建特定地图的配置"""
        return {
            "map_name": map_name,
            "placement_bounds": {
                "min_bounds": [-10000, -10000, 0],
                "max_bounds": [10000, 10000, 2000],
                "default_height": 100
            },
            "exclusion_zones": [
                {
                    "name": "water_area",
                    "bounds": [[-2000, -1000], [2000, 1000]],
                    "reason": "水域区域，避免目标放置"
                }
            ],
            "recommended_trajectories": ["box_trajectory_001", "circular_trajectory_001"],
            "special_settings": {
                "lighting_conditions": "daylight",
                "weather": "clear",
                "time_of_day": "noon"
            }
        }


class ConfigGenerator:
    """配置文件生成器"""
    
    def __init__(self, output_dir: Path):
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)
    
    def generate_pipeline_config(self, filename: str = "pipeline_config.json") -> Path:
        """生成管道配置文件"""
        config = ConfigTemplates.create_default_pipeline_config()
        output_path = self.output_dir / filename
        
        with open(output_path, 'w', encoding='utf-8') as f:
            json.dump(config, f, indent=2, ensure_ascii=False)
        
        return output_path
    
    def generate_trajectory_config(self, filename: str = "trajectories.json") -> Path:
        """生成轨迹配置文件"""
        config = ConfigTemplates.create_multi_trajectory_config()
        output_path = self.output_dir / filename
        
        with open(output_path, 'w', encoding='utf-8') as f:
            json.dump(config, f, indent=2, ensure_ascii=False)
        
        return output_path
    
    def generate_map_configs(self, map_names: List[str]) -> List[Path]:
        """为多个地图生成配置文件"""
        generated_files = []
        
        for map_name in map_names:
            config = ConfigTemplates.create_map_specific_config(map_name)
            filename = f"map_{map_name.lower()}_config.json"
            output_path = self.output_dir / filename
            
            with open(output_path, 'w', encoding='utf-8') as f:
                json.dump(config, f, indent=2, ensure_ascii=False)
            
            generated_files.append(output_path)
        
        return generated_files
    
    def generate_yaml_config(self, config_dict: Dict[str, Any], filename: str) -> Path:
        """生成YAML格式配置文件"""
        output_path = self.output_dir / filename
        
        with open(output_path, 'w', encoding='utf-8') as f:
            yaml.dump(config_dict, f, default_flow_style=False, allow_unicode=True)
        
        return output_path
    
    def generate_all_templates(self) -> Dict[str, Path]:
        """生成所有配置模板"""
        generated_files = {}
        
        # 管道配置
        generated_files['pipeline_config'] = self.generate_pipeline_config()
        
        # 轨迹配置
        generated_files['trajectory_config'] = self.generate_trajectory_config()
        
        # 示例地图配置
        map_files = self.generate_map_configs(['TestMap_01', 'CityMap_Downtown', 'ForestMap_Dense'])
        generated_files['map_configs'] = map_files
        
        # YAML版本的管道配置
        pipeline_config = ConfigTemplates.create_default_pipeline_config()
        generated_files['pipeline_config_yaml'] = self.generate_yaml_config(
            pipeline_config, 'pipeline_config.yaml'
        )
        
        return generated_files


class ConfigValidator:
    """配置验证器"""
    
    @staticmethod
    def validate_pipeline_config(config: Dict[str, Any]) -> List[str]:
        """验证管道配置"""
        errors = []
        
        required_fields = [
            'maps_directory', 'trajectory_file_path', 'height_variants', 'output_directory'
        ]
        
        for field in required_fields:
            if field not in config:
                errors.append(f"缺少必需字段: {field}")
        
        # 验证数值范围
        if 'max_retry_attempts' in config and config['max_retry_attempts'] < 0:
            errors.append("max_retry_attempts 必须为非负数")
        
        if 'scene_stabilization_delay' in config and config['scene_stabilization_delay'] < 0:
            errors.append("scene_stabilization_delay 必须为非负数")
        
        if 'sequence_fps' in config and config['sequence_fps'] <= 0:
            errors.append("sequence_fps 必须为正数")
        
        return errors
    
    @staticmethod
    def validate_trajectory_config(config: Dict[str, Any]) -> List[str]:
        """验证轨迹配置"""
        errors = []
        
        if 'trajectory_type' not in config:
            errors.append("缺少 trajectory_type 字段")
            return errors
        
        trajectory_type = config['trajectory_type']
        parameters = config.get('parameters', {})
        
        # 检查轨迹类型是否有效
        valid_types = ['box', 'line', 'circular', 'custom']
        if trajectory_type not in valid_types:
            errors.append(f"无效的轨迹类型: {trajectory_type}，支持的类型: {', '.join(valid_types)}")
            return errors
        
        # 根据轨迹类型验证参数
        if trajectory_type == 'box':
            required_params = ['line1', 'line2', 'z']
            for param in required_params:
                if param not in parameters:
                    errors.append(f"box轨迹缺少参数: {param}")
        
        elif trajectory_type == 'line':
            required_params = ['point1', 'point2', 'z']
            for param in required_params:
                if param not in parameters:
                    errors.append(f"line轨迹缺少参数: {param}")
        
        elif trajectory_type == 'circular':
            required_params = ['center', 'radius', 'z']
            for param in required_params:
                if param not in parameters:
                    errors.append(f"circular轨迹缺少参数: {param}")
        
        return errors
    
    @staticmethod
    def validate_config_file(file_path: Path) -> List[str]:
        """验证配置文件"""
        errors = []
        
        if not file_path.exists():
            errors.append(f"配置文件不存在: {file_path}")
            return errors
        
        try:
            with open(file_path, 'r', encoding='utf-8') as f:
                if file_path.suffix.lower() == '.json':
                    config = json.load(f)
                elif file_path.suffix.lower() in ['.yaml', '.yml']:
                    config = yaml.safe_load(f)
                else:
                    errors.append(f"不支持的配置文件格式: {file_path.suffix}")
                    return errors
            
            # 根据配置内容判断类型并验证
            if 'trajectory_type' in config:
                errors.extend(ConfigValidator.validate_trajectory_config(config))
            elif 'maps_directory' in config:
                errors.extend(ConfigValidator.validate_pipeline_config(config))
            
        except Exception as e:
            errors.append(f"解析配置文件失败: {e}")
        
        return errors


# 便捷函数
def create_default_configs(output_dir: Path) -> Dict[str, Path]:
    """创建默认配置文件"""
    generator = ConfigGenerator(output_dir)
    return generator.generate_all_templates()


def validate_config(config_path: Path) -> bool:
    """验证配置文件"""
    errors = ConfigValidator.validate_config_file(config_path)
    if errors:
        print("配置验证失败:")
        for error in errors:
            print(f"  - {error}")
        return False
    return True


def create_custom_trajectory(trajectory_type: str, **kwargs) -> Dict[str, Any]:
    """创建自定义轨迹配置"""
    base_config = {
        "box": ConfigTemplates.create_box_trajectory_template,
        "line": ConfigTemplates.create_line_trajectory_template,
        "circular": ConfigTemplates.create_circular_trajectory_template
    }
    
    if trajectory_type not in base_config:
        raise ValueError(f"不支持的轨迹类型: {trajectory_type}")
    
    config = base_config[trajectory_type]()
    
    # 更新参数
    for key, value in kwargs.items():
        if key in config['parameters']:
            config['parameters'][key] = value
        else:
            config[key] = value
    
    return config