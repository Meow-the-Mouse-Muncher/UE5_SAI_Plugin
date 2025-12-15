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
        
        # 5. 遍历每个目标物
        for target_idx, target_actor in enumerate(target_actors, 1):
            target_name = target_actor.get_actor_label()  # 使用标签名称而不是内部名称
            
            # 记录进度
            progress_msg = batch_utils.log_batch_progress(
                map_idx, len(map_list), target_idx, len(target_actors), 
                map_name, target_name, "Starting"
            )
            log_msg_with_socket(PIEExecutor, progress_msg)
            
            # 6. 生成相机轨迹（针对当前目标物）
            level, sequence_name = utils_sequencer.main(target_actor=target_actor, map_name=map_name)
            log_msg_with_socket(PIEExecutor, f'[*] Created Sequence: {sequence_name}')
            
            # 7. 第一次渲染：有遮挡物 (occ)
            unreal.log(f"first time render")
            progress_msg = batch_utils.log_batch_progress(
                map_idx, len(map_list), target_idx, len(target_actors), 
                map_name, target_name, "Rendering with occlusion (occ)"
            )
            log_msg_with_socket(PIEExecutor, progress_msg)
            
            batch_utils.set_actors_visibility(occlusion_actors, visible=True)
            
            # 修改输出路径为 map_target_occ
            render_config_occ = render_config.copy()
            output_suffix = batch_utils.create_output_folder_name(map_name, target_name, "occ")
            render_config_occ['File_Name_Format'] = f"{output_suffix}/{{render_pass}}/{{frame_number}}"
            
            # CustomMoviePipeline.clear_queue()  不要清除队列，等待上一个队列完成
            CustomMoviePipeline.add_job_to_queue_with_render_config(
                level=level,
                level_sequence=sequence_name,
                render_config=render_config_occ
            )
            CustomMoviePipeline.render_queue(executor=PIEExecutor)
            
            # 8. 第二次渲染：无遮挡物 (GT)
            unreal.log(f"second time render")
            progress_msg = batch_utils.log_batch_progress(
                map_idx, len(map_list), target_idx, len(target_actors), 
                map_name, target_name, "Rendering without occlusion (GT)"
            )
            log_msg_with_socket(PIEExecutor, progress_msg)
            
            batch_utils.set_actors_visibility(occlusion_actors, visible=False)
            
            # 修改输出路径为 map_target_GT
            render_config_gt = render_config.copy()
            output_suffix = batch_utils.create_output_folder_name(map_name, target_name, "GT")
            render_config_gt['File_Name_Format'] = f"{output_suffix}/{{render_pass}}/{{frame_number}}"
            
            # CustomMoviePipeline.clear_queue()  不要清除队列，等待上一个队列完成
            CustomMoviePipeline.add_job_to_queue_with_render_config(
                level=level,
                level_sequence=sequence_name,
                render_config=render_config_gt
            )
            CustomMoviePipeline.render_queue(executor=PIEExecutor)
            
            # 恢复遮挡物可见性
            batch_utils.set_actors_visibility(occlusion_actors, visible=True)
            
            progress_msg = batch_utils.log_batch_progress(
                map_idx, len(map_list), target_idx, len(target_actors), 
                map_name, target_name, "Completed"
            )
            log_msg_with_socket(PIEExecutor, progress_msg)
        
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