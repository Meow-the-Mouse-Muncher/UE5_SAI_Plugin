import unreal
import utils_sequencer  # 轨迹脚本
import batch_utils  # 批量处理工具
from pathlib import Path

from GLOBAL_VARS import PLUGIN_ROOT
from data.config import CfgNode
from utils import loader_func, log_msg_with_socket
from custom_movie_pipeline import CustomMoviePipeline # 导入自定义的渲染管线类

unreal.log_warning("######################################################")
unreal.log_warning("### BATCH PIPELINE.PY HAS STARTED EXECUTION ###")
unreal.log_warning("######################################################")

@loader_func
def main(render_config_path):

    # 1. connect to socket
    host = '127.0.0.1'
    port = 9999
    PIEExecutor = unreal.MoviePipelinePIEExecutor()
    PIEExecutor.connect_socket(host, port)
    log_msg_with_socket(PIEExecutor, '[*] Unreal Engine Loaded!')

    # 2. 读取渲染配置
    # if render_config_path is None:
    unreal.log_warning(f"Render Config Path: {render_config_path}")
    render_config_path = str(PLUGIN_ROOT / 'misc/render_SAI_config.yaml')
    

    unreal.log_warning(f"PLUGIN_ROOT: {PLUGIN_ROOT}")
    unreal.log_warning(f"Config path type: {type(render_config_path)}")
    
    # 确保路径是字符串
    render_config_path = str(render_config_path)
    
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
    (cmdTokens, cmdSwitches, cmdParameters) = unreal.SystemLibrary.parse_command_line(
        unreal.SystemLibrary.get_command_line()
    )
    
    # 从命令行参数获取目标地图
    target_map = cmdParameters.get('target_map', '')
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
    
    # 获取目标物
    target_actors = batch_utils.get_target_actors_by_prefix("Target_")
    log_msg_with_socket(PIEExecutor, f'[*] Found {len(target_actors)} targets in {map_name}')
    
    # 获取遮挡物
    occlusion_actors = batch_utils.get_occlusion_actors_by_prefix("SM_")
    log_msg_with_socket(PIEExecutor, f'[*] Found {len(occlusion_actors)} occlusion objects in {map_name}')
    
    # 5. 启动批量目标物串行渲染
    CustomMoviePipeline.start_batch_target_rendering(
        map_name=map_name,
        target_actors=target_actors,
        occlusion_actors=occlusion_actors,
        render_config=render_config,
        executor=PIEExecutor
    )
    
        
    log_msg_with_socket(PIEExecutor, f'[*] Completed processing map: {map_name}')
    log_msg_with_socket(PIEExecutor, '[*] All targets processed successfully!')
    # unreal.SystemLibrary.quit_editor()


if __name__ == "__main__":
    (cmdTokens, cmdSwitches, cmdParameters) = unreal.SystemLibrary.parse_command_line(
        unreal.SystemLibrary.get_command_line()
    )

    render_config_path = cmdParameters.get('render_config_path', '')
    target_map = cmdParameters.get('target_map', '')
    
    unreal.log(f"Using config from command line: {render_config_path}")
    unreal.log(f"Target map from command line: {target_map}")
    
    main(render_config_path)