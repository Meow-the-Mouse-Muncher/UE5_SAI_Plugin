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
    render_config_path = str(PLUGIN_ROOT / 'misc/batch_config.yaml')
    

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
    
    # 3. 获取所有地图
    map_list = batch_utils.get_all_maps_in_folder("/Game/Map")
    log_msg_with_socket(PIEExecutor, f'[*] Found {len(map_list)} maps to process')
    
    current_map_name = None
    
    # 4. 遍历每个地图
    for map_idx, (map_name, map_package_path) in enumerate(map_list, 1):
        log_msg_with_socket(PIEExecutor, f'[*] Processing map {map_idx}/{len(map_list)}: {map_name}')
        
        # 加载地图（如果需要）
        current_map_name = batch_utils.load_map_if_different(map_package_path, current_map_name)
        
        # 获取目标物
        target_actors = batch_utils.get_target_actors_by_prefix("Target_")
        log_msg_with_socket(PIEExecutor, f'[*] Found {len(target_actors)} targets in {map_name}')
        
        # 获取遮挡物
        occlusion_actors = batch_utils.get_occlusion_actors_by_prefix("SM_")
        log_msg_with_socket(PIEExecutor, f'[*] Found {len(occlusion_actors)} occlusion objects in {map_name}')
        
        # 5. 启动批量目标物串行渲染
        CustomMoviePipeline.start_batch_target_rendering(
            map_name=map_name,
            map_package_path=map_package_path,
            target_actors=target_actors,
            occlusion_actors=occlusion_actors,
            render_config=render_config,
            executor=PIEExecutor,
            map_idx=map_idx,
            total_maps=len(map_list)
        )
        
        # 等待当前地图的所有目标物渲染完成
        import time
        while CustomMoviePipeline._is_batch_target_rendering:
            time.sleep(1)  # 每秒检查一次状态
            
        log_msg_with_socket(PIEExecutor, f'[*] Completed processing map: {map_name}')
    
    log_msg_with_socket(PIEExecutor, '[*] All maps and targets processed successfully!')
    # unreal.SystemLibrary.quit_editor()


if __name__ == "__main__":
    (cmdTokens, cmdSwitches, cmdParameters) = unreal.SystemLibrary.parse_command_line(
        unreal.SystemLibrary.get_command_line()
    )

    render_config_path = cmdParameters['render_config_path']
    unreal.log(f"Using config from command line: {render_config_path}")
    
    main(render_config_path)