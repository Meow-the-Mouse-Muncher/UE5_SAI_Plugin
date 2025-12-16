"""
批量处理工具函数
用于处理地图、目标物、遮挡物的批量操作
"""

import unreal
from typing import List, Tuple
from utils import get_world


def get_all_maps_in_folder(folder_path="/Game/Map"):
    """获取指定文件夹下的所有地图 - 使用Python文件系统操作"""
    import os
    from pathlib import Path
    
    # 将UE路径转换为实际文件系统路径
    # /Game/Map -> Content/Map
    if folder_path.startswith("/Game/"):
        relative_path = folder_path.replace("/Game/", "Content/")
    else:
        relative_path = folder_path
    
    # 获取项目根目录
    project_file = Path(unreal.Paths.get_project_file_path())
    project_root = project_file.parent
    map_folder = project_root / relative_path
    
    unreal.log(f"Looking for maps in: {map_folder}")
    
    map_list = []
    
    if map_folder.exists() and map_folder.is_dir():
        # 查找所有.umap文件
        for map_file in map_folder.glob("*.umap"):
            map_name = map_file.stem  # 文件名不含扩展名
            # 转换回UE路径格式
            map_package_path = f"{folder_path}/{map_name}"
            map_list.append((map_name, map_package_path))
            unreal.log(f"Found map: {map_name} -> {map_package_path}")
    else:
        unreal.log_warning(f"Map folder not found: {map_folder}")
        # 使用硬编码的备选方案
        map_list = [("scene_001", "/Game/Map/scene_001")]
        unreal.log_warning("Using fallback map list")
    
    # 按名称排序
    map_list.sort(key=lambda x: x[0])
    unreal.log(f"Total found {len(map_list)} maps: {[name for name, _ in map_list]}")
    return map_list


def get_target_actors_by_prefix(prefix="Target_"):
    """获取当前场景中所有以指定前缀开头的目标物"""
    world = get_world()
    all_actors = unreal.GameplayStatics.get_all_actors_of_class(world, unreal.Actor)
    
    target_actors = []
    for actor in all_actors:
        # 使用get_actor_label()获取编辑器中显示的标签名称
        actor_label = actor.get_actor_label()
        if actor_label.startswith(prefix):
            target_actors.append(actor)
            unreal.log(f"Found target actor: {actor_label} (internal name: {actor.get_name()})")
    
    # 按标签名称排序
    target_actors.sort(key=lambda x: x.get_actor_label())
    unreal.log(f"Total found {len(target_actors)} target actors with prefix '{prefix}'")
    return target_actors


def get_occlusion_actors_by_prefix(prefix="SM_"):
    """获取当前场景中所有以指定前缀开头的遮挡物"""
    world = get_world()
    all_actors = unreal.GameplayStatics.get_all_actors_of_class(world, unreal.StaticMeshActor)
    
    occlusion_actors = []
    for actor in all_actors:
        # 使用get_actor_label()获取编辑器中显示的标签名称
        actor_label = actor.get_actor_label()
        if actor_label.startswith(prefix):
            occlusion_actors.append(actor)
            unreal.log(f"Found occlusion actor: {actor_label} (internal name: {actor.get_name()})")
    
    unreal.log(f"Total found {len(occlusion_actors)} occlusion actors with prefix '{prefix}'")
    return occlusion_actors


def set_actors_visibility(actors: List[unreal.Actor], visible: bool = True):
    """设置actors的可见性
    
    Args:
        actors: 要设置可见性的Actor列表
        visible: True表示可见，False表示隐藏
    """
    unreal.log(f"Setting visibility for {len(actors)} actors to {'VISIBLE' if visible else 'HIDDEN'}")
    
    for actor in actors:
        actor_name = actor.get_actor_label()
        
        # 设置游戏中的可见性（这是渲染时使用的）
        actor.set_actor_hidden_in_game(not visible)
        if isinstance(actor, unreal.StaticMeshActor):
                actor_name = actor.get_actor_label()
                is_hidden = actor.get_editor_property("hidden")
                unreal.log(f"StaticMeshActor '{actor_name}': Hidden 属性的值为: {is_hidden}")



def load_map_if_different(map_package_path: str, current_map_name: str = None):
    """如果当前地图不是目标地图，则加载新地图"""
    if current_map_name is None:
        current_world = unreal.EditorLevelLibrary.get_editor_world()
        current_map_name = current_world.get_name()
    
    # 从包路径中提取地图名
    target_map_name = map_package_path.split('/')[-1]
    
    if current_map_name != target_map_name:
        unreal.log(f"Loading map: {map_package_path}")
        success = unreal.EditorLoadingAndSavingUtils.load_map(map_package_path)
        if not success:
            raise RuntimeError(f"Failed to load map: {map_package_path}")
        unreal.log(f"Map {target_map_name} loaded successfully")
        return target_map_name
    else:
        unreal.log(f"Map {current_map_name} is already loaded")
        return current_map_name


def create_output_folder_name(map_name: str, target_name: str, suffix: str):
    """创建输出文件夹名称"""
    return f"{map_name}_{target_name}_{suffix}"


def log_batch_progress(current_map: int, total_maps: int, current_target: int, total_targets: int, 
                      map_name: str, target_name: str, phase: str):
    """记录批量处理进度"""
    progress_msg = (f"[BATCH PROGRESS] Map {current_map}/{total_maps} - "
                   f"Target {current_target}/{total_targets} - "
                   f"{map_name}::{target_name} - {phase}")
    unreal.log(progress_msg)
    return progress_msg