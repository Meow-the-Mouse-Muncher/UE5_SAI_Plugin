"""
系统集成测试和性能优化

端到端流程测试，性能优化和错误处理完善
"""

import asyncio
import time
import tempfile
import logging
from pathlib import Path
from typing import Dict, List, Any, Optional
from dataclasses import dataclass

from .models import PipelineConfig, MapInfo, TargetObject
from .pipeline_controller import PipelineController
from .config_templates import ConfigGenerator, create_default_configs


@dataclass
class TestResult:
    """测试结果"""
    test_name: str
    success: bool
    execution_time: float
    error_message: Optional[str] = None
    details: Dict[str, Any] = None


class IntegrationTester:
    """集成测试器"""
    
    def __init__(self, test_output_dir: Path):
        self.test_output_dir = Path(test_output_dir)
        self.test_output_dir.mkdir(parents=True, exist_ok=True)
        self.logger = logging.getLogger("IntegrationTester")
        self.test_results: List[TestResult] = []
    
    async def run_all_tests(self) -> Dict[str, Any]:
        """运行所有集成测试"""
        self.logger.info("开始系统集成测试")
        start_time = time.time()
        
        test_methods = [
            self.test_config_system,
            self.test_manager_initialization,
            self.test_pipeline_controller_basic,
            self.test_trajectory_generation,
            self.test_output_management,
            self.test_error_handling,
            self.test_performance_benchmarks
        ]
        
        for test_method in test_methods:
            try:
                result = await test_method()
                self.test_results.append(result)
                
                if result.success:
                    self.logger.info(f"✓ {result.test_name} - 通过 ({result.execution_time:.2f}s)")
                else:
                    self.logger.error(f"✗ {result.test_name} - 失败: {result.error_message}")
                    
            except Exception as e:
                error_result = TestResult(
                    test_name=test_method.__name__,
                    success=False,
                    execution_time=0,
                    error_message=str(e)
                )
                self.test_results.append(error_result)
                self.logger.error(f"✗ {test_method.__name__} - 异常: {e}")
        
        total_time = time.time() - start_time
        return self._generate_test_report(total_time)
    
    async def test_config_system(self) -> TestResult:
        """测试配置系统"""
        start_time = time.time()
        
        try:
            # 创建临时目录
            with tempfile.TemporaryDirectory() as temp_dir:
                temp_path = Path(temp_dir)
                
                # 生成配置文件
                generated_files = create_default_configs(temp_path)
                
                # 验证文件存在
                for file_type, file_path in generated_files.items():
                    if isinstance(file_path, list):
                        for path in file_path:
                            assert path.exists(), f"配置文件不存在: {path}"
                    else:
                        assert file_path.exists(), f"配置文件不存在: {file_path}"
                
                # 验证配置内容
                from .config_templates import ConfigValidator
                pipeline_config = generated_files['pipeline_config']
                errors = ConfigValidator.validate_config_file(pipeline_config)
                assert len(errors) == 0, f"配置验证失败: {errors}"
                
                execution_time = time.time() - start_time
                return TestResult(
                    test_name="配置系统测试",
                    success=True,
                    execution_time=execution_time,
                    details={"generated_files": len(generated_files)}
                )
                
        except Exception as e:
            execution_time = time.time() - start_time
            return TestResult(
                test_name="配置系统测试",
                success=False,
                execution_time=execution_time,
                error_message=str(e)
            )
    
    async def test_manager_initialization(self) -> TestResult:
        """测试管理器初始化"""
        start_time = time.time()
        
        try:
            with tempfile.TemporaryDirectory() as temp_dir:
                temp_path = Path(temp_dir)
                
                # 创建测试配置
                config = self._create_test_config(temp_path)
                
                # 测试各个管理器初始化
                from .managers import (
                    MapManager, TargetManager, OcclusionManager, 
                    TrajectoryManager, OutputManager, OutputConfig
                )
                from .config import ConfigManager
                
                config_manager = ConfigManager()
                
                # Map Manager
                map_manager = MapManager(config_manager)
                map_manager.initialize(config.maps_directory)
                status = map_manager.get_status()
                assert 'maps_directory' in status  # 检查状态包含预期字段
                
                # Target Manager
                target_manager = TargetManager()
                status = target_manager.get_status()
                assert 'retry_counts' in status  # 检查基本状态字段
                
                # Trajectory Manager
                trajectory_manager = TrajectoryManager()
                trajectory_manager.initialize()
                status = trajectory_manager.get_status()
                assert 'sequence_directory' in status
                
                # Output Manager
                output_config = OutputConfig(base_output_dir=str(temp_path / "output"))
                output_manager = OutputManager(output_config)
                output_manager.initialize()
                # Output Manager 初始化成功即可
                
                execution_time = time.time() - start_time
                return TestResult(
                    test_name="管理器初始化测试",
                    success=True,
                    execution_time=execution_time,
                    details={"managers_tested": 4}
                )
                
        except Exception as e:
            execution_time = time.time() - start_time
            return TestResult(
                test_name="管理器初始化测试",
                success=False,
                execution_time=execution_time,
                error_message=str(e)
            )
    
    async def test_pipeline_controller_basic(self) -> TestResult:
        """测试Pipeline Controller基本功能"""
        start_time = time.time()
        
        try:
            with tempfile.TemporaryDirectory() as temp_dir:
                temp_path = Path(temp_dir)
                config = self._create_test_config(temp_path)
                
                # 创建Pipeline Controller
                controller = PipelineController(config)
                
                # 测试状态管理
                status = controller.get_status()
                assert 'state' in status
                assert 'progress' in status
                assert 'managers' in status
                
                # 测试暂停/恢复
                controller.pause_pipeline()
                assert controller.state.is_paused == True
                
                controller.resume_pipeline()
                assert controller.state.is_paused == False
                
                # 测试停止
                controller.stop_pipeline()
                assert controller._shutdown_requested == True
                
                # 测试清理
                await controller._cleanup()
                
                execution_time = time.time() - start_time
                return TestResult(
                    test_name="Pipeline Controller基本功能测试",
                    success=True,
                    execution_time=execution_time,
                    details={"functions_tested": ["status", "pause", "resume", "stop", "cleanup"]}
                )
                
        except Exception as e:
            execution_time = time.time() - start_time
            return TestResult(
                test_name="Pipeline Controller基本功能测试",
                success=False,
                execution_time=execution_time,
                error_message=str(e)
            )
    
    async def test_trajectory_generation(self) -> TestResult:
        """测试轨迹生成"""
        start_time = time.time()
        
        try:
            from .trajectory import TrajectoryFactory, create_box_trajectory, create_line_trajectory
            
            factory = TrajectoryFactory()
            
            # 测试Box轨迹
            box_trajectory = create_box_trajectory(
                line1=[-1000, -1000, 1000, -1000],
                line2=[-1000, 1000, 1000, 1000],
                z=1000
            )
            assert box_trajectory.trajectory_type == "box"
            assert len(box_trajectory.keyframes) > 0
            
            # 测试Line轨迹
            line_trajectory = create_line_trajectory(
                point1=[-1000, 0],
                point2=[1000, 0],
                z=1000
            )
            assert line_trajectory.trajectory_type == "line"
            assert len(line_trajectory.keyframes) > 0
            
            # 测试高度偏移
            offset_trajectory = box_trajectory.apply_height_offset(500)
            assert offset_trajectory.keyframes[0].position[2] == box_trajectory.keyframes[0].position[2] + 500
            
            execution_time = time.time() - start_time
            return TestResult(
                test_name="轨迹生成测试",
                success=True,
                execution_time=execution_time,
                details={"trajectory_types": ["box", "line"], "height_offset_tested": True}
            )
            
        except Exception as e:
            execution_time = time.time() - start_time
            return TestResult(
                test_name="轨迹生成测试",
                success=False,
                execution_time=execution_time,
                error_message=str(e)
            )
    
    async def test_output_management(self) -> TestResult:
        """测试输出管理"""
        start_time = time.time()
        
        try:
            with tempfile.TemporaryDirectory() as temp_dir:
                temp_path = Path(temp_dir)
                
                from .managers import OutputManager, OutputConfig
                
                config = OutputConfig(base_output_dir=str(temp_path))
                output_manager = OutputManager(config)
                
                # 创建测试数据
                map_info = MapInfo(name="TestMap", path="/Content/Map/TestMap")
                target = TargetObject(name="Target_001", location=(0, 0, 100))
                
                # 测试目录创建
                directories = output_manager.create_session_directory(
                    map_info, target, 500.0, "OCC"
                )
                assert len(directories) > 0
                
                # 测试文件名生成
                filename = output_manager.generate_filename(
                    map_info, target, 500.0, "OCC", "fbx"
                )
                assert "TestMap" in filename
                assert "Target_001" in filename
                assert "OCC" in filename
                
                # 测试存储空间检查
                sufficient, available_gb = output_manager.check_storage_space()
                assert isinstance(sufficient, bool)
                assert isinstance(available_gb, float)
                
                execution_time = time.time() - start_time
                return TestResult(
                    test_name="输出管理测试",
                    success=True,
                    execution_time=execution_time,
                    details={"functions_tested": ["directory_creation", "filename_generation", "storage_check"]}
                )
                
        except Exception as e:
            execution_time = time.time() - start_time
            return TestResult(
                test_name="输出管理测试",
                success=False,
                execution_time=execution_time,
                error_message=str(e)
            )
    
    async def test_error_handling(self) -> TestResult:
        """测试错误处理"""
        start_time = time.time()
        
        try:
            error_scenarios_tested = 0
            
            # 测试配置验证器
            from .config_templates import ConfigValidator
            
            # 测试无效管道配置
            invalid_pipeline_config = {"invalid_field": "value"}
            errors = ConfigValidator.validate_pipeline_config(invalid_pipeline_config)
            assert len(errors) > 0, "应该检测到配置错误"
            error_scenarios_tested += 1
            
            # 测试无效轨迹配置
            invalid_trajectory_config = {"trajectory_type": "invalid_type"}
            errors = ConfigValidator.validate_trajectory_config(invalid_trajectory_config)
            assert len(errors) > 0, "应该检测到轨迹配置错误"
            error_scenarios_tested += 1
            
            # 测试缺少必需字段的配置
            incomplete_config = {"maps_directory": "/tmp"}  # 缺少其他必需字段
            errors = ConfigValidator.validate_pipeline_config(incomplete_config)
            assert len(errors) > 0, "应该检测到缺少字段"
            error_scenarios_tested += 1
            
            execution_time = time.time() - start_time
            return TestResult(
                test_name="错误处理测试",
                success=True,
                execution_time=execution_time,
                details={"error_scenarios_tested": error_scenarios_tested}
            )
            
        except Exception as e:
            execution_time = time.time() - start_time
            return TestResult(
                test_name="错误处理测试",
                success=False,
                execution_time=execution_time,
                error_message=str(e)
            )
    
    async def test_performance_benchmarks(self) -> TestResult:
        """测试性能基准"""
        start_time = time.time()
        
        try:
            benchmarks = {}
            
            # 配置生成性能测试
            config_start = time.time()
            with tempfile.TemporaryDirectory() as temp_dir:
                generator = ConfigGenerator(Path(temp_dir))
                generator.generate_all_templates()
            benchmarks['config_generation'] = time.time() - config_start
            
            # 轨迹生成性能测试
            trajectory_start = time.time()
            from .trajectory import create_box_trajectory
            for i in range(10):
                create_box_trajectory(
                    line1=[-1000, -1000, 1000, -1000],
                    line2=[-1000, 1000, 1000, 1000],
                    z=1000 + i * 100
                )
            benchmarks['trajectory_generation_10x'] = time.time() - trajectory_start
            
            # 输出管理性能测试
            output_start = time.time()
            with tempfile.TemporaryDirectory() as temp_dir:
                from .managers import OutputManager, OutputConfig
                config = OutputConfig(base_output_dir=str(temp_dir))
                output_manager = OutputManager(config)
                
                map_info = MapInfo(name="TestMap", path="/Content/Map/TestMap")
                target = TargetObject(name="Target_001", location=(0, 0, 100))
                
                for i in range(5):
                    output_manager.create_session_directory(
                        map_info, target, float(i * 100), "OCC"
                    )
            benchmarks['output_management_5x'] = time.time() - output_start
            
            execution_time = time.time() - start_time
            return TestResult(
                test_name="性能基准测试",
                success=True,
                execution_time=execution_time,
                details={"benchmarks": benchmarks}
            )
            
        except Exception as e:
            execution_time = time.time() - start_time
            return TestResult(
                test_name="性能基准测试",
                success=False,
                execution_time=execution_time,
                error_message=str(e)
            )
    
    def _create_test_config(self, temp_path: Path) -> PipelineConfig:
        """创建测试配置"""
        maps_dir = temp_path / "maps"
        output_dir = temp_path / "output"
        trajectory_file = temp_path / "trajectory.json"
        
        maps_dir.mkdir(exist_ok=True)
        output_dir.mkdir(exist_ok=True)
        trajectory_file.write_text('{"trajectory_type": "box", "parameters": {}}')
        
        return PipelineConfig(
            maps_directory=maps_dir,
            trajectory_file_path=trajectory_file,
            height_variants=[0.0, 500.0],
            output_directory=output_dir,
            max_retry_attempts=1,
            scene_stabilization_delay=0.1,
            min_storage_gb=0.1
        )
    
    def _generate_test_report(self, total_time: float) -> Dict[str, Any]:
        """生成测试报告"""
        passed_tests = [r for r in self.test_results if r.success]
        failed_tests = [r for r in self.test_results if not r.success]
        
        report = {
            "summary": {
                "total_tests": len(self.test_results),
                "passed": len(passed_tests),
                "failed": len(failed_tests),
                "success_rate": len(passed_tests) / len(self.test_results) * 100 if self.test_results else 0,
                "total_execution_time": total_time
            },
            "test_results": [
                {
                    "name": r.test_name,
                    "success": r.success,
                    "execution_time": r.execution_time,
                    "error_message": r.error_message,
                    "details": r.details
                }
                for r in self.test_results
            ],
            "performance_summary": self._extract_performance_data(),
            "recommendations": self._generate_recommendations()
        }
        
        return report
    
    def _extract_performance_data(self) -> Dict[str, Any]:
        """提取性能数据"""
        performance_data = {}
        
        for result in self.test_results:
            if result.details and 'benchmarks' in result.details:
                performance_data.update(result.details['benchmarks'])
        
        return performance_data
    
    def _generate_recommendations(self) -> List[str]:
        """生成优化建议"""
        recommendations = []
        
        failed_tests = [r for r in self.test_results if not r.success]
        if failed_tests:
            recommendations.append(f"修复 {len(failed_tests)} 个失败的测试")
        
        # 性能建议
        performance_data = self._extract_performance_data()
        if 'config_generation' in performance_data:
            if performance_data['config_generation'] > 1.0:
                recommendations.append("配置生成性能较慢，考虑优化模板缓存")
        
        if 'trajectory_generation_10x' in performance_data:
            if performance_data['trajectory_generation_10x'] > 2.0:
                recommendations.append("轨迹生成性能较慢，考虑优化算法或并行处理")
        
        if not recommendations:
            recommendations.append("系统性能良好，无需特别优化")
        
        return recommendations


class SystemOptimizer:
    """系统优化器"""
    
    @staticmethod
    def optimize_config_loading():
        """优化配置加载"""
        # 实现配置缓存机制
        pass
    
    @staticmethod
    def optimize_trajectory_generation():
        """优化轨迹生成"""
        # 实现轨迹生成缓存和并行处理
        pass
    
    @staticmethod
    def optimize_memory_usage():
        """优化内存使用"""
        # 实现内存池和对象复用
        pass


# 便捷函数
async def run_integration_tests(output_dir: Optional[Path] = None) -> Dict[str, Any]:
    """运行集成测试"""
    if output_dir is None:
        output_dir = Path("test_results")
    
    tester = IntegrationTester(output_dir)
    return await tester.run_all_tests()


def generate_test_report(test_results: Dict[str, Any], output_file: Path) -> None:
    """生成测试报告文件"""
    import json
    
    with open(output_file, 'w', encoding='utf-8') as f:
        json.dump(test_results, f, indent=2, ensure_ascii=False)
    
    print(f"测试报告已生成: {output_file}")