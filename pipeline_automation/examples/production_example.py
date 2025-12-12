#!/usr/bin/env python3
"""
生产环境Pipeline示例

展示如何在生产环境中使用Pipeline Automation System
包含完整的错误处理、监控和恢复机制
"""

import asyncio
import logging
import signal
import sys
import json
import time
from pathlib import Path
from datetime import datetime
from typing import Optional

# 添加路径
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from pipeline_automation import (
    PipelineController, PipelineConfig, ConfigManager,
    ProgressTracker, create_default_configs
)


class ProductionPipelineRunner:
    """生产环境管道运行器"""
    
    def __init__(self, config_dir: Path):
        self.config_dir = Path(config_dir)
        self.controller: Optional[PipelineController] = None
        self.progress_tracker: Optional[ProgressTracker] = None
        self.shutdown_requested = False
        
        # 设置日志
        self._setup_logging()
        
        # 设置信号处理
        self._setup_signal_handlers()
    
    def _setup_logging(self):
        """设置生产环境日志"""
        log_dir = self.config_dir.parent / "logs"
        log_dir.mkdir(exist_ok=True)
        
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        log_file = log_dir / f"pipeline_production_{timestamp}.log"
        
        logging.basicConfig(
            level=logging.INFO,
            format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
            handlers=[
                logging.FileHandler(log_file),
                logging.StreamHandler()
            ]
        )
        
        self.logger = logging.getLogger("ProductionPipeline")
        self.logger.info(f"生产环境日志初始化: {log_file}")
    
    def _setup_signal_handlers(self):
        """设置信号处理器"""
        def signal_handler(signum, frame):
            self.logger.info(f"接收到信号 {signum}，开始优雅关闭...")
            self.shutdown_requested = True
            if self.controller:
                self.controller.stop_pipeline()
        
        signal.signal(signal.SIGINT, signal_handler)
        signal.signal(signal.SIGTERM, signal_handler)
    
    async def load_and_validate_config(self) -> PipelineConfig:
        """加载并验证配置"""
        config_file = self.config_dir / "pipeline_config.json"
        
        if not config_file.exists():
            self.logger.error(f"配置文件不存在: {config_file}")
            raise FileNotFoundError(f"配置文件不存在: {config_file}")
        
        self.logger.info(f"加载配置文件: {config_file}")
        
        with open(config_file, 'r') as f:
            config_data = json.load(f)
        
        # 验证必需的生产环境配置
        required_fields = [
            'project_path', 'ue_executable_path', 'maps_directory',
            'trajectory_file_path', 'output_directory'
        ]
        
        missing_fields = [field for field in required_fields if field not in config_data]
        if missing_fields:
            raise ValueError(f"配置文件缺少必需字段: {missing_fields}")
        
        # 验证路径存在
        path_fields = {
            'project_path': '项目文件',
            'ue_executable_path': 'UE5可执行文件',
            'maps_directory': '地图目录'
        }
        
        for field, description in path_fields.items():
            path = Path(config_data[field])
            if not path.exists():
                raise FileNotFoundError(f"{description}不存在: {path}")
        
        # 创建PipelineConfig对象
        config = PipelineConfig(
            maps_directory=Path(config_data['maps_directory']),
            trajectory_file_path=Path(config_data['trajectory_file_path']),
            height_variants=config_data.get('height_variants', [0.0, 500.0, 1000.0]),
            output_directory=Path(config_data['output_directory']),
            max_retry_attempts=config_data.get('max_retry_attempts', 3),
            scene_stabilization_delay=config_data.get('scene_stabilization_delay', 2.0),
            target_name_pattern=config_data.get('target_name_pattern', 'Target_*'),
            occlusion_name_pattern=config_data.get('occlusion_name_pattern', 'SM_*'),
            sequence_fps=config_data.get('sequence_fps', 24.0),
            default_camera_fov=config_data.get('default_camera_fov', 90.0),
            project_path=Path(config_data['project_path']),
            ue_executable_path=Path(config_data['ue_executable_path']),
            min_storage_gb=config_data.get('min_storage_gb', 10.0),
            map_filter=config_data.get('map_filter', [])
        )
        
        self.logger.info("配置验证通过")
        return config
    
    async def run_pipeline(self) -> bool:
        """运行管道"""
        try:
            # 加载配置
            config = await self.load_and_validate_config()
            
            # 创建Pipeline Controller
            self.logger.info("初始化Pipeline Controller...")
            self.controller = PipelineController(config)
            
            # 创建进度跟踪器
            self.progress_tracker = ProgressTracker()
            
            # 显示初始状态
            status = self.controller.get_status()
            self.logger.info(f"系统初始化完成 - 管理器数量: {len(status['managers'])}")
            
            # 启动进度监控任务
            monitor_task = asyncio.create_task(self._monitor_progress())
            
            # 运行管道
            self.logger.info("开始执行管道流程...")
            result = await self.controller.run_pipeline()
            
            # 停止监控
            monitor_task.cancel()
            
            # 处理结果
            if result.success:
                self.logger.info("✅ 管道执行成功完成")
                self.logger.info(f"总会话: {result.total_sessions}, "
                               f"成功: {result.successful_sessions}, "
                               f"失败: {result.failed_sessions}")
                self.logger.info(f"执行时间: {result.execution_time:.2f}秒")
                return True
            else:
                self.logger.error("❌ 管道执行失败")
                self.logger.error(f"错误信息: {result.error_message}")
                return False
                
        except Exception as e:
            self.logger.error(f"管道执行异常: {e}")
            import traceback
            self.logger.error(traceback.format_exc())
            return False
        
        finally:
            if self.controller:
                await self.controller._cleanup()
    
    async def _monitor_progress(self):
        """监控执行进度"""
        try:
            while not self.shutdown_requested:
                if self.controller:
                    status = self.controller.get_status()
                    progress = status['progress']
                    
                    self.logger.info(f"进度更新 - 完成会话: {progress['completed_sessions']}, "
                                   f"失败会话: {progress['failed_sessions']}")
                
                await asyncio.sleep(30)  # 每30秒报告一次进度
                
        except asyncio.CancelledError:
            self.logger.info("进度监控已停止")
    
    async def run_with_recovery(self, max_retries: int = 3) -> bool:
        """带恢复机制的运行"""
        for attempt in range(max_retries):
            if self.shutdown_requested:
                break
                
            try:
                self.logger.info(f"开始执行尝试 {attempt + 1}/{max_retries}")
                
                success = await self.run_pipeline()
                if success:
                    return True
                    
                if attempt < max_retries - 1:
                    self.logger.warning(f"尝试 {attempt + 1} 失败，等待后重试...")
                    await asyncio.sleep(60)  # 等待1分钟后重试
                    
            except Exception as e:
                self.logger.error(f"尝试 {attempt + 1} 异常: {e}")
                if attempt < max_retries - 1:
                    await asyncio.sleep(60)
        
        self.logger.error("所有重试尝试都失败了")
        return False


async def main():
    """主函数"""
    print("🏭 Pipeline Automation System - 生产环境模式")
    print("=" * 60)
    
    # 检查配置目录
    config_dir = Path("pipeline_example/config")
    if not config_dir.exists():
        print(f"❌ 配置目录不存在: {config_dir}")
        return
    
    # 创建运行器
    runner = ProductionPipelineRunner(config_dir)
    
    # 运行管道（带恢复机制）
    success = await runner.run_with_recovery(max_retries=3)
    
    if success:
        print("✅ 生产管道执行成功完成")
        sys.exit(0)
    else:
        print("❌ 生产管道执行失败")
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())