import sys

# Unreal's embedded Python may ignore PYTHONDONTWRITEBYTECODE.
sys.dont_write_bytecode = True

import unreal
import utils_sequencer  # 轨迹脚本
import batch_utils  # 批量处理工具
import render_worker
import os
from pathlib import Path

from GLOBAL_VARS import PLUGIN_ROOT
from data.config import CfgNode
from utils import log_msg_with_socket
from custom_movie_pipeline import CustomMoviePipeline # 导入自定义的渲染管线类

unreal.log_warning("######################################################")
unreal.log_warning("### BATCH PIPELINE.PY HAS STARTED EXECUTION ###")
unreal.log_warning("######################################################")

_editor_viewport_diagnostic_applied = False


def _apply_editor_viewport_diagnostic():
    """Optionally stop continuous editor viewport redraws for a diagnostic run.

    MRQ owns a separate PIE viewport. Explicit editor invalidations can still
    draw once while realtime is overridden; this does not disable all drawing.
    """
    global _editor_viewport_diagnostic_applied
    if (_editor_viewport_diagnostic_applied
            or os.environ.get('MATRIXCITY_DIAGNOSTIC_DISABLE_EDITOR_REALTIME') != '1'):
        return
    subsystem = unreal.get_editor_subsystem(unreal.LevelEditorSubsystem)
    viewport_keys = list(subsystem.get_viewport_config_keys())
    if not viewport_keys:
        raise RuntimeError('Editor realtime diagnostic requested but no level editor viewports were found')
    for key in viewport_keys:
        subsystem.editor_set_viewport_realtime(False, key)
    _editor_viewport_diagnostic_applied = True
    unreal.log_warning('[ViewportDiagnostic] editor_realtime_override=False viewport_count={} viewport_keys={}'.format(
        len(viewport_keys), [str(key) for key in viewport_keys]))


def main(render_config_path):

    (cmdTokens, cmdSwitches, cmdParameters) = unreal.SystemLibrary.parse_command_line(
        unreal.SystemLibrary.get_command_line()
    )

    worker_config = render_worker.configure_worker(cmdParameters.get('worker_config_path'))

    # 1. connect to the worker-specific socket when one was supplied.
    host = '127.0.0.1'
    try:
        port = int(cmdParameters.get('socket_port', '9999'))
    except (TypeError, ValueError):
        unreal.log_warning("Invalid socket_port; falling back to 9999")
        port = 9999
    PIEExecutor = unreal.MoviePipelinePIEExecutor()
    import render_cancellation
    render_cancellation.install(PIEExecutor)
    PIEExecutor.connect_socket(host, port)
    CustomMoviePipeline.host = host
    CustomMoviePipeline.port = port
    log_msg_with_socket(PIEExecutor, '[*] Unreal Engine Loaded!')

    # 2. 读取渲染配置
    unreal.log_warning(f"Render Config Path: {render_config_path}")
    if not render_config_path:
        render_config_path = PLUGIN_ROOT / 'misc/render_SAI_config.yaml'
    render_config_path = str(render_config_path)
    unreal.log_warning(f"Using render config: {render_config_path}")
    
    try:
        render_config = CfgNode.load_yaml_with_base(render_config_path)
        unreal.log(f"Successfully loaded config file")
    except Exception as e:
        unreal.log_error(f"Failed to load config file: {e}")
        # 使用默认配置作为备选
        render_config_path = str(PLUGIN_ROOT / 'misc/render_config_common.yaml')
        unreal.log_warning(f"Trying fallback config: {render_config_path}")
        render_config = CfgNode.load_yaml_with_base(render_config_path)
    
    # 3. 获取指定地图路径
    # 从命令行参数获取目标地图
    target_map = worker_config.get('map_package_path') or cmdParameters.get('target_map', '')
    if target_map:
        map_name = target_map.split('/')[-1]  # 从路径中提取地图名称
        map_package_path = target_map
        log_msg_with_socket(PIEExecutor, f'[*] Processing specified map: {map_name}')
    else:
        # 如果没有指定地图，使用当前加载的地图
        current_world = unreal.EditorLevelLibrary.get_editor_world()
        map_name = current_world.get_name()
        map_package_path = f"/Game/Map/{map_name}"
        log_msg_with_socket(PIEExecutor, f'[*] Processing current map: {map_name}')
    
    # 4. 处理指定地图
    # 确保地图已加载（如果指定了地图路径）
    if target_map:
        current_map_name = batch_utils.load_map_if_different(map_package_path, None)

    _apply_editor_viewport_diagnostic()
    
    # 获取目标物
    target_actors = batch_utils.get_target_actors_by_prefix("Target_")
    log_msg_with_socket(PIEExecutor, f'[*] Found {len(target_actors)} targets in {map_name}')
    
    # 获取遮挡物
    occlusion_actors = batch_utils.get_occlusion_actors_by_prefix("SM_")
    log_msg_with_socket(PIEExecutor, f'[*] Found {len(occlusion_actors)} occlusion objects in {map_name}')

    # Keep the complete actor list for OCC/GT visibility; selection is applied
    # only when building the render task queue.
    render_config['Performance_Settings'] = render_worker.performance_settings(render_config)
    
    # 5. 启动批量目标物串行渲染
    CustomMoviePipeline.start_batch_target_rendering(
        map_name=map_name,
        target_actors=target_actors,
        occlusion_actors=occlusion_actors,
        render_config=render_config,
        executor=PIEExecutor
    )
    
        
    log_msg_with_socket(PIEExecutor, f'[*] Render tasks submitted for map: {map_name}')
    # unreal.SystemLibrary.quit_editor()


_pipeline_start_handle = None


def start_when_assets_ready(render_config_path):
    """Capture config explicitly: Slate passes delta_seconds to tick callbacks."""
    global _pipeline_start_handle

    def on_start_tick(_delta_seconds=0.0):
        global _pipeline_start_handle
        if unreal.AssetRegistryHelpers.get_asset_registry().is_loading_assets():
            return
        handle = _pipeline_start_handle
        if handle is None:
            return
        _pipeline_start_handle = None
        unreal.unregister_slate_pre_tick_callback(handle)
        try:
            main(render_config_path)
        except Exception:
            import traceback
            unreal.log_error(traceback.format_exc())
            # Startup errors must not leave a headless editor waiting forever.
            try:
                _, _, parameters = unreal.SystemLibrary.parse_command_line(
                    unreal.SystemLibrary.get_command_line())
                failure_executor = unreal.MoviePipelinePIEExecutor()
                failure_executor.connect_socket('127.0.0.1', int(parameters.get('socket_port', '9999')))
                failure_executor.send_socket_message('Pipeline Failed')
            finally:
                unreal.SystemLibrary.quit_editor()

    _pipeline_start_handle = unreal.register_slate_pre_tick_callback(on_start_tick)
    on_start_tick()


if __name__ == "__main__":
    (cmdTokens, cmdSwitches, cmdParameters) = unreal.SystemLibrary.parse_command_line(
        unreal.SystemLibrary.get_command_line()
    )

    render_config_path = cmdParameters.get('render_config_path', '')
    target_map = cmdParameters.get('target_map', '')
    
    unreal.log(f"Using config from command line: {render_config_path}")
    unreal.log(f"Target map from command line: {target_map}")
    
    start_when_assets_ready(render_config_path)
