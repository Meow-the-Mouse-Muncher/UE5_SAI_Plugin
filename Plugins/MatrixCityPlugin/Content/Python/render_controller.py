"""
渲染控制器 
提供灵活的渲染流程控制，支持复杂的场景配置和自定义规则
"""

import unreal
from typing import List, Dict, Any, Callable, Optional
from enum import Enum

# 简单的数据类替代dataclass（兼容性更好）
class SceneConfig:
    """场景配置"""
    def __init__(self, target_actors_visibility=None, occlusion_actors_visibility=None, 
                 camera_sequence=None, custom_settings=None):
        self.target_actors_visibility = target_actors_visibility or {}
        self.occlusion_actors_visibility = occlusion_actors_visibility or {}
        self.camera_sequence = camera_sequence
        self.custom_settings = custom_settings or {}

class RenderStep:
    """渲染步骤"""
    def __init__(self, step_id, step_type, scene_config, render_config=None, 
                 level_path=None, sequence_path=None, pre_step_callback=None, 
                 post_step_callback=None, custom_action=None):
        self.step_id = step_id
        self.step_type = step_type
        self.scene_config = scene_config
        self.render_config = render_config
        self.level_path = level_path
        self.sequence_path = sequence_path
        self.pre_step_callback = pre_step_callback
        self.post_step_callback = post_step_callback
        self.custom_action = custom_action


class RenderStepType(Enum):
    """渲染步骤类型"""
    SCENE_SETUP = "scene_setup"      # 场景配置
    RENDER_OCC = "render_occ"        # 遮挡渲染
    RENDER_GT = "render_gt"          # 真值渲染
    CUSTOM = "custom"                # 自定义步骤


class RenderState(Enum):
    """渲染状态"""
    IDLE = "idle"
    PREPARING = "preparing"
    RENDERING = "rendering"
    PROCESSING = "processing"
    COMPLETED = "completed"
    FAILED = "failed"





class RenderController:
    """渲染控制器 - 协程模拟模式"""
    
    def __init__(self, executor=None):
        self.state = RenderState.IDLE
        self.render_steps = []
        self.current_step_index = 0
        self.current_context = {}
        self.executor = executor
        
        # 场景状态缓存
        self._scene_actors_cache = {}
        self._current_scene_config = None
        
        # 回调函数
        self.on_sequence_complete = None
        self.on_sequence_failed = None
        self.on_step_complete = None
    
    def add_render_sequence(self, steps):
        """添加渲染序列"""
        self.render_steps = steps
        self.current_step_index = 0
        self.state = RenderState.IDLE
        unreal.log(f"Added render sequence with {len(steps)} steps")
    
    def start_rendering(self):
        """开始渲染序列"""
        if self.state != RenderState.IDLE:
            unreal.log_warning(f"Cannot start rendering, current state: {self.state}")
            return False
        
        if not self.render_steps:
            unreal.log_error("No render steps defined")
            return False
        
        unreal.log("Starting render sequence...")
        self.state = RenderState.PREPARING
        self.current_step_index = 0
        self._execute_current_step()
        return True
    
    def _execute_current_step(self):
        """执行当前步骤"""
        if self.current_step_index >= len(self.render_steps):
            self._on_sequence_complete()
            return
        
        current_step = self.render_steps[self.current_step_index]
        unreal.log(f"Executing step {self.current_step_index + 1}/{len(self.render_steps)}: {current_step.step_id}")
        
        try:
            # 1. 执行步骤前回调
            if current_step.pre_step_callback:
                current_step.pre_step_callback(current_step, self.current_context)
            
            # 2. 应用场景配置
            self._apply_scene_config(current_step.scene_config)
            
            # 3. 执行步骤动作
            if current_step.step_type == RenderStepType.SCENE_SETUP:
                self._execute_scene_setup(current_step)
            elif current_step.step_type == RenderStepType.RENDER_OCC:
                self._execute_render_step(current_step, "OCC")
            elif current_step.step_type == RenderStepType.RENDER_GT:
                self._execute_render_step(current_step, "GT")
            elif current_step.step_type == RenderStepType.CUSTOM:
                self._execute_custom_step(current_step)
            
        except Exception as e:
            unreal.log_error(f"Step execution failed: {e}")
            self.state = RenderState.FAILED
            if self.on_sequence_failed:
                self.on_sequence_failed(e)
    
    def _apply_scene_config(self, scene_config):
        """应用场景配置 - 确保场景状态正确"""
        unreal.log("Applying scene configuration...")
        
        try:
            import batch_utils
            
            # 应用目标物可见性
            if scene_config.target_actors_visibility:
                for actor_name, visible in scene_config.target_actors_visibility.items():
                    actor = self._get_actor_by_name(actor_name)
                    if actor:
                        batch_utils.set_actors_visibility([actor], visible=visible)
                        unreal.log(f"Set target actor '{actor_name}' visibility: {visible}")
            
            # 应用遮挡物可见性
            if scene_config.occlusion_actors_visibility:
                for actor_name, visible in scene_config.occlusion_actors_visibility.items():
                    actor = self._get_actor_by_name(actor_name)
                    if actor:
                        batch_utils.set_actors_visibility([actor], visible=visible)
                        unreal.log(f"Set occlusion actor '{actor_name}' visibility: {visible}")
            
            # 缓存当前场景配置
            self._current_scene_config = scene_config
            unreal.log("Scene configuration applied successfully")
            
        except ImportError as e:
            unreal.log_error(f"Failed to import batch_utils: {e}")
            # 回退到内置方法
            self._apply_scene_config_fallback(scene_config)
    
    def _apply_scene_config_fallback(self, scene_config):
        """场景配置应用的回退方法"""
        unreal.log("Using fallback scene configuration method...")
        
        # 应用目标物可见性
        if scene_config.target_actors_visibility:
            for actor_name, visible in scene_config.target_actors_visibility.items():
                actor = self._get_actor_by_name(actor_name)
                if actor:
                    self._set_actor_visibility(actor, visible)
                    unreal.log(f"Set target actor '{actor_name}' visibility: {visible}")
        
        # 应用遮挡物可见性
        if scene_config.occlusion_actors_visibility:
            for actor_name, visible in scene_config.occlusion_actors_visibility.items():
                actor = self._get_actor_by_name(actor_name)
                if actor:
                    self._set_actor_visibility(actor, visible)
                    unreal.log(f"Set occlusion actor '{actor_name}' visibility: {visible}")
        
        # 缓存当前场景配置
        self._current_scene_config = scene_config
        unreal.log("Scene configuration applied successfully")
    
    def _execute_scene_setup(self, step: RenderStep):
        """执行场景设置步骤"""
        unreal.log(f"Scene setup step: {step.step_id}")
        
        # 场景设置通常是同步的，直接进入下一步
        self._on_step_complete(step)
    
    def _execute_render_step(self, step: RenderStep, render_type: str):
        """执行渲染步骤"""
        unreal.log(f"Starting {render_type} render: {step.step_id}")
        
        if not step.render_config or not step.level_path or not step.sequence_path:
            raise ValueError(f"Missing render configuration for step: {step.step_id}")
        
        self.state = RenderState.RENDERING
        
        # 动态导入避免循环依赖
        from custom_movie_pipeline import CustomMoviePipeline
        
        # 清空队列并添加渲染任务
        CustomMoviePipeline.clear_queue()
        CustomMoviePipeline.add_job_to_queue_with_render_config(
            level=step.level_path,
            level_sequence=step.sequence_path,
            render_config=step.render_config
        )
        
        # 启动渲染（回调会通过CustomMoviePipeline的onQueueFinishedCallback处理）
        CustomMoviePipeline.render_queue(executor=self.executor)
        
        # 发送Socket消息
        if self.executor:
            try:
                from utils import log_msg_with_socket
                log_msg_with_socket(self.executor, f"Started {render_type} render: {step.step_id}")
            except Exception as e:
                unreal.log_warning(f"Socket message failed: {e}")
    
    def _execute_custom_step(self, step: RenderStep):
        """执行自定义步骤"""
        unreal.log(f"Custom step: {step.step_id}")
        
        if step.custom_action:
            try:
                result = step.custom_action(step, self.current_context)
                # 如果自定义动作返回False，表示异步操作，需要手动调用完成
                if result is not False:
                    self._on_step_complete(step)
            except Exception as e:
                unreal.log_error(f"Custom step failed: {e}")
                self.state = RenderState.FAILED
        else:
            # 没有自定义动作，直接完成
            self._on_step_complete(step)
    
    def _on_render_complete(self, step: RenderStep, success: bool):
        """渲染完成回调"""
        if success:
            unreal.log(f"Render step completed successfully: {step.step_id}")
            self._on_step_complete(step)
        else:
            unreal.log_error(f"Render step failed: {step.step_id}")
            self.state = RenderState.FAILED
            if self.on_sequence_failed:
                self.on_sequence_failed(f"Render step failed: {step.step_id}")
    
    def _on_step_complete(self, step: RenderStep):
        """步骤完成处理"""
        unreal.log(f"Step completed: {step.step_id}")
        
        # 执行步骤后回调
        if step.post_step_callback:
            try:
                step.post_step_callback(step, self.current_context)
            except Exception as e:
                unreal.log_error(f"Post-step callback failed: {e}")
        
        # 调用外部步骤完成回调
        if self.on_step_complete:
            self.on_step_complete(step, self.current_context)
        
        # 移动到下一步
        self.current_step_index += 1
        self.state = RenderState.PREPARING
        
        # 执行下一步
        self._execute_current_step()
    
    def _on_sequence_complete(self):
        """序列完成处理"""
        unreal.log("Render sequence completed successfully!")
        self.state = RenderState.COMPLETED
        
        if self.on_sequence_complete:
            self.on_sequence_complete(self.current_context)
    
    def _get_actor_by_name(self, actor_name: str):
        """根据名称获取Actor"""
        if actor_name in self._scene_actors_cache:
            return self._scene_actors_cache[actor_name]
        
        # 查找Actor
        actor_system = unreal.get_editor_subsystem(unreal.EditorActorSubsystem)
        all_actors = actor_system.get_all_level_actors()
        
        for actor in all_actors:
            if actor.get_actor_label() == actor_name:
                self._scene_actors_cache[actor_name] = actor
                return actor
        
        unreal.log_warning(f"Actor not found: {actor_name}")
        return None
    
    def _set_actor_visibility(self, actor, visible):
        """设置Actor可见性"""
        if actor:
            actor.set_actor_hidden_in_game(not visible)
    
    def manual_step_complete(self):
        """手动完成当前步骤 - 用于异步自定义步骤"""
        if self.state == RenderState.RENDERING or self.state == RenderState.PROCESSING:
            current_step = self.render_steps[self.current_step_index]
            self._on_step_complete(current_step)
    
    def get_current_step(self):
        """获取当前步骤"""
        if 0 <= self.current_step_index < len(self.render_steps):
            return self.render_steps[self.current_step_index]
        return None
    
    def get_progress(self):
        """获取进度 (current, total)"""
        return (self.current_step_index, len(self.render_steps))


class RenderSequenceBuilder:
    """渲染序列构建器 - 简化序列创建"""
    
    def __init__(self):
        self.steps = []
    
    def add_scene_setup(self, step_id, scene_config, 
                       pre_callback=None, post_callback=None):
        """添加场景设置步骤"""
        step = RenderStep(
            step_id=step_id,
            step_type=RenderStepType.SCENE_SETUP,
            scene_config=scene_config,
            pre_step_callback=pre_callback,
            post_step_callback=post_callback
        )
        self.steps.append(step)
        return self
    
    def add_occ_render(self, step_id, scene_config, 
                      render_config, level_path, sequence_path,
                      pre_callback=None, post_callback=None):
        """添加遮挡渲染步骤"""
        step = RenderStep(
            step_id=step_id,
            step_type=RenderStepType.RENDER_OCC,
            scene_config=scene_config,
            render_config=render_config,
            level_path=level_path,
            sequence_path=sequence_path,
            pre_step_callback=pre_callback,
            post_step_callback=post_callback
        )
        self.steps.append(step)
        return self
    
    def add_gt_render(self, step_id, scene_config,
                     render_config, level_path, sequence_path,
                     pre_callback=None, post_callback=None):
        """添加真值渲染步骤"""
        step = RenderStep(
            step_id=step_id,
            step_type=RenderStepType.RENDER_GT,
            scene_config=scene_config,
            render_config=render_config,
            level_path=level_path,
            sequence_path=sequence_path,
            pre_step_callback=pre_callback,
            post_step_callback=post_callback
        )
        self.steps.append(step)
        return self
    
    def add_custom_step(self, step_id, scene_config,
                       custom_action, pre_callback=None, post_callback=None):
        """添加自定义步骤"""
        step = RenderStep(
            step_id=step_id,
            step_type=RenderStepType.CUSTOM,
            scene_config=scene_config,
            custom_action=custom_action,
            pre_step_callback=pre_callback,
            post_step_callback=post_callback
        )
        self.steps.append(step)
        return self
    
    def build(self):
        """构建渲染步骤列表"""
        return self.steps.copy()