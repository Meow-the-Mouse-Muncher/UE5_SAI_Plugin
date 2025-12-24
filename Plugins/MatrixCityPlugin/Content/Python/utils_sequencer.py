from typing import *
from pydantic_model import SequenceKey
import unreal
import math
import json # 导入 json
from pathlib import Path # 导入 Path

from GLOBAL_VARS import PLUGIN_ROOT
from utils import *
from utils_actor import *
import math
import numpy as np
import random
import yaml

################################################################################
# misc

def convert_frame_rate_to_fps(frame_rate: unreal.FrameRate) -> float:
    return frame_rate.numerator / frame_rate.denominator


def get_sequence_fps(sequence: unreal.LevelSequence) -> float:
    seq_fps: unreal.FrameRate = sequence.get_display_rate()
    return convert_frame_rate_to_fps(seq_fps)
    

def get_animation_length(animation_asset: unreal.AnimSequence, seq_fps: Optional[float]=None) -> int:
    anim_len = animation_asset.get_editor_property("number_of_sampled_frames")

    if seq_fps:
        anim_frame_rate = animation_asset.get_editor_property("target_frame_rate")
        anim_frame_rate = convert_frame_rate_to_fps(anim_frame_rate)
        if anim_frame_rate != seq_fps:
            anim_len = round(animation_asset.get_editor_property("sequence_length") * seq_fps)

    return anim_len


def load_trajectory_config() -> Dict[str, Any]:
    """Load trajectory config YAML from plugin misc folder.

    Returns an empty dict on failure or if file not present.
    """
    yaml_file = PLUGIN_ROOT / 'misc' / 'trajectory_config.yaml'
    try:
        if not yaml_file.exists():
            unreal.log_warning(f"trajectory config not found: {yaml_file}")
            return {}
        with open(yaml_file, 'r') as f:
            data = yaml.safe_load(f) or {}
        unreal.log(f"Loaded trajectory config: {yaml_file}")
        return data
    except Exception as e:
        unreal.log_error(f"Failed to load trajectory config: {e}")
        return {}


################################################################################
# sequencer session
def get_transform_channels_from_section(
    trans_section: unreal.MovieScene3DTransformSection,
) -> List[unreal.MovieSceneScriptingChannel]:
    channel_x = channel_y = channel_z = channel_roll = channel_pitch = channel_yaw = None
    # [修复] UE5.x 中 get_channels() 已废弃，使用 get_channels_by_type() 替代
    for channel in trans_section.get_channels_by_type(unreal.MovieSceneScriptingDoubleChannel):
        channel: unreal.MovieSceneScriptingChannel
        if channel.channel_name == "Location.X":
            channel_x = channel
        elif channel.channel_name == "Location.Y":
            channel_y = channel
        elif channel.channel_name == "Location.Z":
            channel_z = channel
        elif channel.channel_name == "Rotation.X":
            channel_roll = channel
        elif channel.channel_name == "Rotation.Y":
            channel_pitch = channel
        elif channel.channel_name == "Rotation.Z":
            channel_yaw = channel
    assert channel_x is not None, "channel_x is None"
    assert channel_y is not None, "channel_y is None"
    assert channel_z is not None, "channel_z is None"
    assert channel_roll is not None, "channel_roll is None"
    assert channel_pitch is not None, "channel_pitch is None"
    assert channel_yaw is not None, "channel_yaw is None"

    return channel_x, channel_y, channel_z, channel_roll, channel_pitch, channel_yaw

def set_transform_by_section(
    trans_section: unreal.MovieScene3DTransformSection,
    loc: Tuple[float, float, float],
    rot: Tuple[float, float, float],
    key_frame: int = 0,
    key_type: str = "CONSTANT",
) -> None:
    """set `loc & rot` keys to given `transform section`

    Args:
        trans_section (unreal.MovieScene3DTransformSection): section
        loc (tuple): location key
        rot (tuple): rotation key
        key_frame (int): frame of the key. Defaults to 0.
        key_type (str): type of the key. Defaults to 'CONSTANT'. Choices: 'CONSTANT', 'LINEAR', 'AUTO'.
    """

    channel_x, channel_y, channel_z, \
        channel_roll, channel_pitch, channel_yaw = get_transform_channels_from_section(trans_section)

    loc_x, loc_y, loc_z = loc
    rot_x, rot_y, rot_z = rot

    key_frame_ = unreal.FrameNumber(key_frame)
    key_type_ = getattr(unreal.MovieSceneKeyInterpolation, key_type)
    channel_x.add_key(key_frame_, loc_x, interpolation=key_type_)
    channel_y.add_key(key_frame_, loc_y, interpolation=key_type_)
    channel_z.add_key(key_frame_, loc_z, interpolation=key_type_)
    channel_roll.add_key(key_frame_, rot_x, interpolation=key_type_)
    channel_pitch.add_key(key_frame_, rot_y, interpolation=key_type_)
    channel_yaw.add_key(key_frame_, rot_z, interpolation=key_type_)


def set_transforms_by_section(
    trans_section: unreal.MovieScene3DTransformSection,
    trans_keys: List[SequenceKey], 
    key_type: str = "CONSTANT",
) -> None:
    """set `loc & rot` keys to given `transform section`

    Args:
        trans_section (unreal.MovieScene3DTransformSection): section
        trans_dict (dict): keys
        type (str): type of the key. Defaults to 'CONSTANT'. Choices: 'CONSTANT', 'LINEAR', 'AUTO'.

    Examples:
        >>> sequence = unreal.load_asset('/Game/Sequences/NewSequence')
        >>> camera_binding = sequence.add_spawnable_from_class(unreal.CameraActor)
        >>> transform_track: unreal.MovieScene3DTransformTrack = camera_binding.add_track(unreal.MovieScene3DTransformTrack)
        >>> transform_section: unreal.MovieScene3DTransformSection = transform_track.add_section()
        >>> trans_dict = {
        >>>     0: [                    # time of key
        >>>         [500, 1500, 100],   # location of key
        >>>         [0, 0, 30]          # rotation of key
        >>>     ],
        >>>     300: [                     # multi-keys
        >>>         [1000, 2000, 300],
        >>>         [0, 0, 0]
        >>>     ]
        >>> }
        >>> set_transforms_by_section(transform_section, trans_dict)
    """

    channel_x, channel_y, channel_z, \
        channel_roll, channel_pitch, channel_yaw = get_transform_channels_from_section(trans_section)
    key_type_ = getattr(unreal.MovieSceneKeyInterpolation, key_type)

    for trans_key in trans_keys:
        key_frame = trans_key.frame
        loc_x, loc_y, loc_z = trans_key.location
        rot_x, rot_y, rot_z = trans_key.rotation

        key_time_ = unreal.FrameNumber(key_frame)
        channel_x.add_key(key_time_, loc_x, interpolation=key_type_)
        channel_y.add_key(key_time_, loc_y, interpolation=key_type_)
        channel_z.add_key(key_time_, loc_z, interpolation=key_type_)
        channel_roll.add_key(key_time_, rot_x, interpolation=key_type_)
        channel_pitch.add_key(key_time_, rot_y, interpolation=key_type_)
        channel_yaw.add_key(key_time_, rot_z, interpolation=key_type_)


def set_transform_by_binding(
    binding: unreal.SequencerBindingProxy,
    loc: Tuple[float, float, float],
    rot: Tuple[float, float, float],
    key_frame: int = 0,
    key_type: str = "CONSTANT",
) -> None:
    trans_track: unreal.MovieScene3DTransformTrack = binding.find_tracks_by_type(
        unreal.MovieScene3DTransformTrack)[0]
    trans_section = trans_track.get_sections()[0]
    set_transform_by_section(trans_section, loc, rot, key_frame, key_type)


def set_transform_by_key(
    sequence: unreal.MovieSceneSequence,
    key: str,
    loc: Tuple[float, float, float],
    rot: Tuple[float, float, float],
    key_frame: int = 0,
    key_type: str = "CONSTANT",
) -> None:
    binding: unreal.SequencerBindingProxy = sequence.find_binding_by_name(key)
    set_transform_by_binding(binding, loc, rot, key_frame, key_type)


def add_property_bool_track_to_binding(
    binding: unreal.SequencerBindingProxy,
    property_name: str,
    property_value: bool,
    bool_track: Optional[unreal.MovieSceneBoolTrack] = None,
) -> unreal.MovieSceneBoolTrack:

    if bool_track is None:
        # add bool track
        bool_track: unreal.MovieSceneBoolTrack = binding.add_track(unreal.MovieSceneBoolTrack)
        bool_track.set_property_name_and_path(property_name, property_name)

    # add bool section, and set it to extend the whole sequence
    bool_section = bool_track.add_section()
    bool_section.set_start_frame_bounded(0)
    bool_section.set_end_frame_bounded(0)

    # set key
    for channel in bool_section.get_channels_by_type(unreal.MovieSceneScriptingBoolChannel):
        channel.set_default(property_value)
    
    return bool_track


def add_property_int_track_to_binding(
    binding: unreal.SequencerBindingProxy,
    property_name: str,
    property_value: int,
    int_track: Optional[unreal.MovieSceneIntegerTrack] = None,
) -> unreal.MovieSceneIntegerTrack:

    if int_track is None:
        # add int track
        int_track: unreal.MovieSceneIntegerTrack = binding.add_track(unreal.MovieSceneIntegerTrack)
        int_track.set_property_name_and_path(property_name, property_name)

    # add int section, and set it to extend the whole sequence
    int_section = int_track.add_section()
    int_section.set_start_frame_bounded(0)
    int_section.set_end_frame_bounded(0)

    # set key
    for channel in int_section.get_channels_by_type(unreal.MovieSceneScriptingIntegerChannel):
        channel.set_default(property_value)
    
    return int_track


def add_property_float_track_to_binding(
    binding: unreal.SequencerBindingProxy,
    property_name: str,
    property_value: float,
    float_track: Optional[unreal.MovieSceneFloatTrack] = None,
) -> unreal.MovieSceneFloatTrack:
    if float_track is None:
        float_track = binding.add_track(unreal.MovieSceneFloatTrack)
        float_track.set_property_name_and_path(property_name, property_name)

    float_section = float_track.add_section()
    float_section.set_range(0, 100000)  # infinite range

    # [修复] 使用 get_channels_by_type() 替代已废弃的 find_channels_by_type(...)
    # for channel in float_section.find_channels_by_type(unreal.MovieSceneScriptingFloatChannel):
    for channel in float_section.get_channels_by_type(unreal.MovieSceneScriptingFloatChannel):
        channel.set_default(property_value)

    return float_track


def add_transform_to_binding(
    binding: unreal.SequencerBindingProxy,
    actor_loc: Tuple[float, float, float],
    actor_rot: Tuple[float, float, float],
    seq_end_frame: int,
    seq_start_frame: int=0,
    time: int = 0,
    key_type: str = "CONSTANT",
) -> unreal.MovieScene3DTransformTrack:
    """Add a transform track to the binding, and add one key at `time` to the track.

    Args:
        binding (unreal.SequencerBindingProxy): The binding to add the track to.
        actor_loc (Tuple[float, float, float]): The location of the actor.
        actor_rot (Tuple[float, float, float]): The rotation of the actor.
        seq_end_frame (int): The end frame of the sequence.
        seq_start_frame (int, optional): The start frame of the sequence. Defaults to 0.
        time (int, optional): The time of the key. Defaults to 0.
        key_type (str, optional): The type of the key. Defaults to "CONSTANT".

    Returns:
        transform_track (unreal.MovieScene3DTransformTrack): The transform track.
    """

    transform_track: unreal.MovieScene3DTransformTrack = binding.add_track(unreal.MovieScene3DTransformTrack)
    transform_section: unreal.MovieScene3DTransformSection = transform_track.add_section()
    transform_section.set_end_frame(seq_end_frame)
    transform_section.set_start_frame(seq_start_frame)
    set_transform_by_section(transform_section, actor_loc, actor_rot, time, key_type)

    return transform_track


def add_transforms_to_binding(
    binding: unreal.SequencerBindingProxy,
    actor_trans_keys: List[SequenceKey],
    key_type: str = "CONSTANT",
) -> unreal.MovieScene3DTransformTrack:

    transform_track: unreal.MovieScene3DTransformTrack = binding.add_track(unreal.MovieScene3DTransformTrack)
    transform_section: unreal.MovieScene3DTransformSection = transform_track.add_section()
    # set infinite
    transform_section.set_start_frame_bounded(0)
    transform_section.set_end_frame_bounded(0)
    # add keys
    set_transforms_by_section(transform_section, actor_trans_keys, key_type)

    return transform_track


def add_animation_to_binding(
    binding: unreal.SequencerBindingProxy,
    animation_asset: unreal.AnimSequence,
    animation_length: Optional[int]=None,
    seq_fps: Optional[float]=None,
) -> None:
    animation_track: unreal.MovieSceneSkeletalAnimationTrack = binding.add_track(
        track_type=unreal.MovieSceneSkeletalAnimationTrack
    )
    animation_section: unreal.MovieSceneSkeletalAnimationSection = animation_track.add_section()
    animation_length_ = get_animation_length(animation_asset, seq_fps)
    if animation_length is None:
        animation_length = animation_length_
    if animation_length > animation_length_:
        unreal.log_error(f"animation: '{animation_asset.get_name()}' length is too short, it will repeat itself!")

    params = unreal.MovieSceneSkeletalAnimationParams()
    params.set_editor_property("Animation", animation_asset)
    animation_section.set_editor_property("Params", params)
    animation_section.set_range(0, animation_length)


def get_spawnable_actor_from_binding(
    sequence: unreal.MovieSceneSequence,
    binding: unreal.SequencerBindingProxy,
) -> unreal.Actor:

    binds = unreal.Array(unreal.SequencerBindingProxy)
    binds.append(binding)

    bound_objects: List[unreal.SequencerBoundObjects] = unreal.SequencerTools.get_bound_objects(
        get_world(), 
        sequence, 
        binds, 
        sequence.get_playback_range()
    )

    actor = bound_objects[0].bound_objects[0]
    return actor

################################################################################
# high level functions

def add_level_visibility_to_sequence(
    sequence: unreal.LevelSequence, 
    seq_length: Optional[int]=None,
) -> None:

    if seq_length is None:
        seq_length = sequence.get_playback_end()

    # add master track (level visibility) to sequence
    # [修复] add_master_track 已废弃，使用 add_track 替代
    level_visibility_track: unreal.MovieSceneLevelVisibilityTrack = sequence.add_track(unreal.MovieSceneLevelVisibilityTrack)
    # add level visibility section
    level_visible_section: unreal.MovieSceneLevelVisibilitySection = level_visibility_track.add_section()
    level_visible_section.set_visibility(unreal.LevelVisibility.VISIBLE)
    level_visible_section.set_start_frame(-1)
    level_visible_section.set_end_frame(seq_length)

    level_hidden_section: unreal.MovieSceneLevelVisibilitySection = level_visibility_track.add_section()
    level_hidden_section.set_row_index(1)
    level_hidden_section.set_visibility(unreal.LevelVisibility.HIDDEN)
    level_hidden_section.set_start_frame(-1)
    level_hidden_section.set_end_frame(seq_length)
    return level_visible_section, level_hidden_section


def add_level_to_sequence(
    sequence: unreal.LevelSequence, 
    persistent_level_path: str, 
    new_level_path: str,
    seq_fps: Optional[float]=None,
    seq_length: Optional[int]=None,
) -> None:
    """creating a new level which contains the persistent level as sub-levels.
    `CAUTION`: this function can't support `World Partition` type level which is new in unreal 5.
        No warning/error would be printed if `World partition` is used, but it will not work.

    Args:
        sequence (unreal.LevelSequence): _description_
        persistent_level_path (str): _description_
        new_level_path (str): _description_
        seq_fps (Optional[float], optional): _description_. Defaults to None.
        seq_length (Optional[int], optional): _description_. Defaults to None.
    """

    # get sequence settings
    if seq_fps is None:
        seq_fps = get_sequence_fps(sequence)
    if seq_length is None:
        seq_length = sequence.get_playback_end()

    # create a new level to place actors
    success = new_world(new_level_path)
    print(f"new level: '{new_level_path}' created: {success}")
    assert success, RuntimeError("Failed to create level")

    level_visible_names, level_hidden_names = add_levels(persistent_level_path, new_level_path)
    level_visible_section, level_hidden_section = add_level_visibility_to_sequence(sequence, seq_length)

    # set level visibility
    level_visible_section.set_level_names(level_visible_names)
    level_hidden_section.set_level_names(level_hidden_names)

    # set created level as current level
    world = get_world()
    levels = get_levels(world)
    unreal.SF_BlueprintFunctionLibrary.set_level(world, levels[0])
    save_current_level()




def add_possessable_camera_to_sequence(
    sequence: unreal.LevelSequence,
    camera_actor: unreal.CameraActor,
    camera_trans: List[SequenceKey],
    seq_length: Optional[int]=None,
    key_type: str="CONSTANT",
) -> None:
    """
    将场景中已存在的相机绑定到序列中
    """
    # get sequence settings
    if seq_length is None:
        seq_length = sequence.get_playback_end()

    # add possessable binding (绑定现有相机)
    camera_binding = sequence.add_possessable(camera_actor)
    
    # add camera cut track to sequence
    camera_cut_track = sequence.add_track(unreal.MovieSceneCameraCutTrack)

    # add a camera cut track for this camera
    camera_cut_section = camera_cut_track.add_section()
    camera_cut_section.set_start_frame(-1)
    camera_cut_section.set_end_frame(seq_length)

    # set the camera cut to use this camera
    camera_binding_id = unreal.MovieSceneObjectBindingID()
    camera_binding_id.set_editor_property("Guid", camera_binding.get_id())
    camera_cut_section.set_editor_property("CameraBindingID", camera_binding_id)

    # set the camera location and rotation (应用轨迹)
    add_transforms_to_binding(camera_binding, camera_trans, key_type)



def generate_sequence(
    sequence_dir: str, 
    sequence_name: str, 
    seq_fps: float,
    seq_length: int,
) -> unreal.LevelSequence:

    asset_tools: unreal.AssetTools = unreal.AssetToolsHelpers.get_asset_tools()  # type: ignore
    
    # Check if asset exists and delete it if it does to ensure overwrite
    full_path = f"{sequence_dir}/{sequence_name}"
    if unreal.EditorAssetLibrary.does_asset_exist(full_path):
        unreal.EditorAssetLibrary.delete_asset(full_path)
        unreal.log_warning(f"Deleted existing sequence: {full_path}")

    new_sequence: unreal.LevelSequence = asset_tools.create_asset(
        sequence_name,
        sequence_dir,
        unreal.LevelSequence,
        unreal.LevelSequenceFactoryNew(),
    )
    assert (new_sequence is not None), f"Failed to create LevelSequence: {sequence_dir}, {sequence_name}"
    # Set sequence config
    new_sequence.set_display_rate(unreal.FrameRate(seq_fps))
    new_sequence.set_playback_end(seq_length)

    return new_sequence

def fix_line(target_actor, num_frames, angle_degrees, height_offset, trajectory_length, current_frame=0):
    """
    在目标物正上方生成直线轨迹
    
    Args:
        target_actor: 目标物Actor
        num_frames (int): 图像张数（帧数）
        angle_degrees (float): 与x轴的夹角（度）- 直线的方向角度
        height_offset (float): 相对于目标物的高度偏移（UE单位：cm）
        trajectory_length (float): 运动轨迹的长度（UE单位：cm）
        current_frame (int): 起始帧数，默认为0
    
    Returns:
        tuple: (camera_trans, end_frame) - 相机轨迹列表和结束帧数
    """
    # 获取目标物位置
    target_location = target_actor.get_actor_location()
    target_x, target_y, target_z = target_location.x, target_location.y, target_location.z
    
    # 计算相机高度
    camera_z = target_z + height_offset
    
    # 将角度转换为弧度
    angle_radians = math.radians(angle_degrees)
    
    # 计算直线的方向向量
    dx = math.cos(angle_radians)
    dy = math.sin(angle_radians)
    
    # 修正：计算起点和终点，确保起点在angle_degrees的反方向
    # 这样轨迹从angle_degrees的反方向开始，向angle_degrees方向移动
    half_length = trajectory_length / 2.0
    
    # 起点：从目标物沿angle_degrees反方向偏移half_length
    start_x = target_x - half_length * dx
    start_y = target_y - half_length * dy
    
    # 终点：从目标物沿angle_degrees正方向偏移half_length  
    end_x = target_x + half_length * dx
    end_y = target_y + half_length * dy
    
    # 生成轨迹点
    camera_trans = []
    
    # 用于存储前一帧的 yaw 角度，处理奇点情况
    previous_yaw = 0.0
    
    for i in range(num_frames):
        # 计算当前帧的插值比例 (0.0 到 1.0)
        t = i / (num_frames - 1) if num_frames > 1 else 0.0
        
        # 线性插值计算当前位置
        camera_x = start_x + t * (end_x - start_x)
        camera_y = start_y + t * (end_y - start_y)
        
        # 计算相机朝向目标物的旋转角度
        # 计算从相机到目标物的向量
        look_vector_x = target_x - camera_x
        look_vector_y = target_y - camera_y
        look_vector_z = target_z - camera_z
        
        # 计算俯仰角（pitch）
        horizontal_distance = math.sqrt(look_vector_x**2 + look_vector_y**2)
        pitch = -90
        
        # 计算偏航角（yaw）- 处理奇点情况
        if horizontal_distance < 1e-6:  # 相机在目标物正上方（奇点）
            yaw = previous_yaw  # 使用前一帧的 yaw 角度
        else:
            yaw = math.degrees(math.atan2(look_vector_y, look_vector_x))
            previous_yaw = yaw  # 更新前一帧的 yaw 角度
        
        # 翻滚角保持为0
        roll = 0
        
        # 添加轨迹点
        camera_trans.append(
            SequenceKey(
                frame=current_frame + i,
                location=(camera_x, camera_y, camera_z),
                rotation=(roll, pitch, yaw)
            )
        )
    
    end_frame = current_frame + num_frames
    return camera_trans, end_frame


def rot_line(target_actor, num_frames, arc_angle_degrees, height, plane_angle_degrees, current_frame=0):
    """
    以目标物为中心生成直线轨迹，相机始终对准目标物
    按角度均匀采样生成直线上的点，相机在固定高度的水平面上移动
    
    Args:
        target_actor: 目标物Actor
        num_frames (int): 图像张数（帧数）
        arc_angle_degrees (float): 角度范围（度），用于角度均匀采样
        height (float): 相机相对于目标物的垂直高度偏移（UE单位：cm）
        plane_angle_degrees (float): 直线与X轴的夹角（度）
            - 0度: 直线沿X轴方向
            - 90度: 直线沿Y轴方向
        current_frame (int): 起始帧数，默认为0
    
    Returns:
        tuple: (camera_trans, end_frame) - 相机轨迹列表和结束帧数
    """
    # 获取目标物位置
    target_location = target_actor.get_actor_location()
    target_x, target_y, target_z = target_location.x, target_location.y, target_location.z
    
    # 计算相机高度（固定高度偏移）
    camera_z = target_z + height
    
    # 将角度转换为弧度
    arc_angle_radians = math.radians(arc_angle_degrees)
    plane_angle_radians = math.radians(plane_angle_degrees)
    
    # 计算直线的长度（基于高度和角度范围）
    # 使用高度作为参考距离，根据角度范围计算直线长度
    reference_distance = height  # 使用高度作为参考距离
    line_length = 2.0 * reference_distance * math.tan(arc_angle_radians / 2.0)
    
    # 计算直线的起点和终点（在水平面上）
    # 直线方向向量
    line_direction_x = math.cos(plane_angle_radians)
    line_direction_y = math.sin(plane_angle_radians)
    
    # 直线中心点（目标物在水平面上的投影）
    line_center_x = target_x
    line_center_y = target_y
    
    # 计算起点和终点
    half_length = line_length / 2.0
    start_x = line_center_x - half_length * line_direction_x
    start_y = line_center_y - half_length * line_direction_y
    end_x = line_center_x + half_length * line_direction_x
    end_y = line_center_y + half_length * line_direction_y
    
    # 生成轨迹点
    camera_trans = []
    
    # 用于存储前一帧的 yaw 角度，处理奇点情况
    previous_yaw = 0.0
    
    for i in range(num_frames):
        # 按角度均匀采样的思路：
        # 1. 先计算角度采样点
        # 2. 将角度采样点映射到直线上的位置
        if num_frames > 1:
            angle_t = i / (num_frames - 1)  # 0.0 到 1.0
        else:
            angle_t = 0.0
        
        # 将角度采样转换为直线上的位置参数
        # 使用正弦函数映射，使得角度均匀采样对应直线上的非均匀采样
        # 这样可以保持与rot_arc相似的角度采样特性
        current_angle = -arc_angle_radians / 2.0 + angle_t * arc_angle_radians
        
        # 将角度映射到直线位置参数 t (0.0 到 1.0)
        # 使用正弦函数的反函数来实现角度到位置的映射
        if arc_angle_radians > 0:
            position_t = (math.sin(current_angle) + math.sin(arc_angle_radians / 2.0)) / (2.0 * math.sin(arc_angle_radians / 2.0))
        else:
            position_t = angle_t
        
        # 确保position_t在[0,1]范围内
        position_t = max(0.0, min(1.0, position_t))
        
        # 根据位置参数计算相机在直线上的位置
        camera_x = start_x + position_t * (end_x - start_x)
        camera_y = start_y + position_t * (end_y - start_y)
        
        # 计算相机朝向目标物的旋转角度
        # 计算从相机到目标物的向量
        look_vector_x = target_x - camera_x
        look_vector_y = target_y - camera_y
        look_vector_z = target_z - camera_z
        
        # 计算俯仰角（pitch）
        horizontal_distance = math.sqrt(look_vector_x**2 + look_vector_y**2)
        pitch = math.degrees(math.atan2(look_vector_z, horizontal_distance))
        
        # 计算偏航角（yaw）- 处理奇点情况
        if horizontal_distance < 1e-6:  # 相机在目标物正上方（奇点）
            yaw = previous_yaw  # 使用前一帧的 yaw 角度
        else:
            yaw = math.degrees(math.atan2(look_vector_y, look_vector_x))
            previous_yaw = yaw  # 更新前一帧的 yaw 角度
        
        # 翻滚角保持为0
        roll = 0.0
        
        # 添加轨迹点
        camera_trans.append(
            SequenceKey(
                frame=current_frame + i,
                location=(camera_x, camera_y, camera_z),
                rotation=(roll, pitch, yaw)
            )
        )
    
    end_frame = current_frame + num_frames
    return camera_trans, end_frame


def rot_arc(target_actor, num_frames, arc_angle_degrees, radius, plane_angle_degrees, current_frame=0):
    """
    以目标物为中心生成圆弧轨迹，相机始终对准目标物
    圆弧轨迹在包含目标物Z轴的垂直平面内
    
    Args:
        target_actor: 目标物Actor
        num_frames (int): 图像张数（帧数）
        arc_angle_degrees (float): 圆弧的角度范围（度）
        radius (float): 圆弧半径（UE单位：cm）
        plane_angle_degrees (float): 垂直平面与X轴的夹角（度）
            - 0度: 圆弧在XZ平面内，起点朝向X轴正方向
            - 90度: 圆弧在YZ平面内，起点朝向Y轴正方向
        current_frame (int): 起始帧数，默认为0
    
    Returns:
        tuple: (camera_trans, end_frame) - 相机轨迹列表和结束帧数
    """
    # 获取目标物位置
    target_location = target_actor.get_actor_location()
    target_x, target_y, target_z = target_location.x, target_location.y, target_location.z
    
    # 将角度转换为弧度
    arc_angle_radians = math.radians(arc_angle_degrees)
    plane_angle_radians = math.radians(plane_angle_degrees)
    
    # 计算圆弧的起始角度（以垂直向上90度为中心）
    # 这样圆弧的中心点在目标物正上方，相机在圆弧上看向地面目标物
    center_angle = math.pi / 2.0  # 90度，垂直向上
    start_angle = center_angle - arc_angle_radians / 2.0
    end_angle = center_angle + arc_angle_radians / 2.0
    
    # 生成轨迹点
    camera_trans = []
    
    # 用于存储前一帧的 yaw 角度，处理奇点情况
    previous_yaw = 0.0
    
    for i in range(num_frames):
        # 计算当前帧在圆弧上的角度
        if num_frames > 1:
            t = i / (num_frames - 1)  # 0.0 到 1.0
        else:
            t = 0.0
        
        current_angle = start_angle + t * (end_angle - start_angle)
        
        # 在垂直平面的局部坐标系中计算圆弧上的点
        # 局部坐标系：水平方向为local_r，垂直方向为local_z
        local_r = radius * math.cos(current_angle)  # 水平距离（从目标物向外的距离）
        local_z = radius * math.sin(current_angle)  # 垂直距离（相对于目标物的高度）
        
        # 将局部坐标转换到世界坐标系
        # 根据plane_angle_degrees确定水平方向在XY平面的投影
        camera_x = target_x + local_r * math.cos(plane_angle_radians)
        camera_y = target_y + local_r * math.sin(plane_angle_radians)
        camera_z = target_z + local_z
        
        # 计算相机朝向目标物的旋转角度
        # 计算从相机到目标物的向量
        look_vector_x = target_x - camera_x
        look_vector_y = target_y - camera_y
        look_vector_z = target_z - camera_z
        
        # 计算俯仰角（pitch）
        horizontal_distance = math.sqrt(look_vector_x**2 + look_vector_y**2)
        pitch = math.degrees(math.atan2(look_vector_z, horizontal_distance))
        
        # 计算偏航角（yaw）- 处理奇点情况
        if horizontal_distance < 1e-6:  # 相机在目标物正上方（奇点）
            yaw = previous_yaw  # 使用前一帧的 yaw 角度
        else:
            yaw = math.degrees(math.atan2(look_vector_y, look_vector_x))
            previous_yaw = yaw  # 更新前一帧的 yaw 角度
        
        # 翻滚角保持为0
        roll = 0.0
        
        # 添加轨迹点
        camera_trans.append(
            SequenceKey(
                frame=current_frame + i,
                location=(camera_x, camera_y, camera_z),
                rotation=(roll, pitch, yaw)
            )
        )
    
    end_frame = current_frame + num_frames
    return camera_trans, end_frame





def generate_single_trajectory(target_actor, map_name, trajectory_type, trajectory_params, camera_height, num_frames):
    """生成单个轨迹类型的序列
    
    Args:
        target_actor: 目标物Actor
        map_name: 地图名称
        trajectory_type: 轨迹类型 ('fix_line', 'rot_arc', 'rot_line')
        trajectory_params: 轨迹参数
        camera_height: 相机高度（共用参数）
        num_frames: 帧数（共用参数）
    
    Returns:
        tuple: (level, sequence_name) - 关卡路径和序列名称
    """
    config_file = PLUGIN_ROOT / 'misc/user.json'
    with open(config_file, 'r') as f:
        config = json.load(f)

    # 如果没有传入地图名，从配置中获取
    if map_name is None:
        level = config.get('ue_map', '/Game/Map/main')
    else:
        level = f'/Game/Map/{map_name}'
        
    # 从配置中获取相机名称，默认为 'CineCameraActor1'
    target_camera_name = config.get('camera_name', 'CineCameraActor1')
    
    # 根据轨迹类型设置序列保存目录
    sequence_base_dir = '/Game/Sequences'
    sequence_dir = f'{sequence_base_dir}/{trajectory_type}'  # 按轨迹类型分文件夹
    
    seq_fps = 24
    current_frame = 0
    
    # 如果已经在正确的地图中，就不需要重新加载
    current_world = unreal.EditorLevelLibrary.get_editor_world()
    current_level_name = current_world.get_name()
    
    if map_name and current_level_name != map_name:
        unreal.log(f"正在加载地图: {level} ...")
        if not unreal.EditorLoadingAndSavingUtils.load_map(level):
            error_msg = f"CRITICAL ERROR: 无法加载地图: {level}。请检查路径是否正确。"
            unreal.log_error(error_msg)
            raise RuntimeError(error_msg)
        unreal.log(f"地图 {level} 加载成功。")
    
    # 根据地图名和目标物名生成序列名称，格式：scene_001_Target_002_height_50
    if map_name and target_actor:
        target_name = target_actor.get_actor_label()
        # 将相机高度从cm转换为米，并格式化为3位数
        height_in_meters = str(int(camera_height / 100.0)).zfill(3)
        sequence_name = f'{map_name}_{target_name}_height_{height_in_meters}'
    else:
        height_in_meters = str(int(camera_height / 100.0)).zfill(3)
        sequence_name = f'aerial_train_{height_in_meters}'  # 默认名称
    
    # 根据轨迹类型生成相机轨迹
    camera_trans = None
    if target_actor:
        if trajectory_type == 'fix_line':
            # 使用fix_line轨迹
            angle_degrees = trajectory_params.get('angle_degrees', 0.0)
            trajectory_length = trajectory_params.get('trajectory_length', 4000.0)
            
            camera_trans, current_frame = fix_line(
                target_actor=target_actor,
                num_frames=num_frames,
                angle_degrees=angle_degrees,
                height_offset=camera_height,  # 使用共用的camera_height
                trajectory_length=trajectory_length,
                current_frame=current_frame
            )
        elif trajectory_type == 'rot_arc':
            # 使用rot_arc轨迹
            arc_angle_degrees = trajectory_params.get('arc_angle_degrees', 90.0)
            plane_angle_degrees = trajectory_params.get('plane_angle_degrees', 0.0)
            
            camera_trans, current_frame = rot_arc(
                target_actor=target_actor,
                num_frames=num_frames,
                arc_angle_degrees=arc_angle_degrees,
                radius=camera_height,  # 使用共用的camera_height
                plane_angle_degrees=plane_angle_degrees,
                current_frame=current_frame
            )
        elif trajectory_type == 'rot_line':
            # 使用rot_line轨迹
            arc_angle_degrees = trajectory_params.get('arc_angle_degrees', 90.0)
            plane_angle_degrees = trajectory_params.get('plane_angle_degrees', 0.0)
            
            camera_trans, current_frame = rot_line(
                target_actor=target_actor,
                num_frames=num_frames,
                arc_angle_degrees=arc_angle_degrees,
                height=camera_height,  # 使用共用的camera_height
                plane_angle_degrees=plane_angle_degrees,
                current_frame=current_frame
            )

    seq_length = current_frame  # 使用计算出的序列长度
    
    # 确保序列目录存在
    try:
        # 检查目录是否存在，如果不存在则创建
        if not unreal.EditorAssetLibrary.does_directory_exist(sequence_dir):
            unreal.EditorAssetLibrary.make_directory(sequence_dir)
            unreal.log(f"Created sequence directory: {sequence_dir}")
    except Exception as e:
        unreal.log_warning(f"Failed to create sequence directory {sequence_dir}: {e}")
    
    # 生成新的序列资产，保证长度fps等设置
    new_sequence = generate_sequence(sequence_dir, sequence_name, seq_fps, seq_length) 

    # 查找并使用场景中的 CineCameraActor
    found_camera = None
    actor_system = unreal.get_editor_subsystem(unreal.EditorActorSubsystem)
    
    # 遍历查找相机
    unreal.log(f"正在查找相机: {target_camera_name} ...")
    # 1. 获取场景中所有的 Actor
    all_actors = actor_system.get_all_level_actors()
    
    # 2. 筛选出所有相机类型的 Actor (包括 CineCameraActor)
    all_cameras = [actor for actor in all_actors if isinstance(actor, unreal.CameraActor)]

    # 3. 遍历相机查找匹配的名字
    for camera in all_cameras:
        # 获取大纲视图中的名字 (Label)
        camera_label = camera.get_actor_label()
        
        # 比较名字 (strip() 去除可能不小心输入的空格)
        if camera_label.strip() == target_camera_name.strip():
            found_camera = camera
            break
            
    if found_camera:
        unreal.log(f"SUCCESS: 已找到相机: {target_camera_name} (ID: {found_camera.get_name()})")
        print(f"Camera FOV: {found_camera.camera_component.field_of_view}")
        
        if not camera_trans:
            unreal.log_warning("WARNING: 相机轨迹数据 (camera_trans) 为空！相机将不会移动。")

        # 使用 add_possessable_camera_to_sequence 绑定现有相机
        add_possessable_camera_to_sequence(
            new_sequence, 
            camera_actor=found_camera,
            camera_trans=camera_trans,
            seq_length=seq_length,
            key_type="LINEAR"
        )
    else:
        found_camera_names = [c.get_actor_label() for c in all_cameras]
        error_msg = (
            f"CRITICAL ERROR: 未找到名为 '{target_camera_name}' 的相机！\n"
            f"当前场景中发现的相机有: {found_camera_names}\n"
            f"请检查大纲视图(Outliner)中的名称是否完全一致（注意空格）。"
        )
        unreal.log_error(error_msg)
        # 抛出异常以终止流程，防止生成无效序列导致渲染问题
        raise RuntimeError(error_msg)

    # 保存修改后的序列资产
    unreal.EditorAssetLibrary.save_loaded_asset(new_sequence, False)

    return level, f'{sequence_dir}/{sequence_name}'


def main(target_actor=None, map_name=None, trajectory_type=None, trajectory_params=None):
    """生成所有三种轨迹和多种高度的序列
    
    Args:
        target_actor: 目标物Actor
        map_name: 地图名称
        trajectory_type: 轨迹类型（已废弃，现在生成所有三种）
        trajectory_params: 轨迹参数（已废弃，从YAML读取）
    
    Returns:
        dict: 包含所有轨迹类型和高度的序列信息 {(trajectory_type, height): (level, sequence_name)}
    """
    # 加载轨迹配置
    trajectory_cfg = load_trajectory_config()
    if not isinstance(trajectory_cfg, dict):
        unreal.log_error("Failed to load trajectory config")
        return {}
    
    # 获取全局参数
    global_params = trajectory_cfg.get('global', {})
    camera_heights = global_params.get('camera_heights', [5000.0])  # 默认单一高度
    num_frames = global_params.get('num_frames', 32)
    
    unreal.log(f"Using global params: camera_heights={camera_heights}, num_frames={num_frames}")
    
    # 定义要生成的轨迹类型
    trajectory_types = ['fix_line', 'rot_arc', 'rot_line']
    results = {}
    
    # 为每种轨迹类型和每种高度生成序列
    for traj_type in trajectory_types:
        for camera_height in camera_heights:
            height_in_meters = str(int(camera_height / 100.0)).zfill(3)
            unreal.log(f"Generating trajectory: {traj_type} at height {height_in_meters}m")
            
            # 获取该轨迹类型的参数
            traj_params = trajectory_cfg.get(traj_type, {})
            
            try:
                # 生成单个轨迹序列
                level, sequence_name = generate_single_trajectory(
                    target_actor=target_actor,
                    map_name=map_name,
                    trajectory_type=traj_type,
                    trajectory_params=traj_params,
                    camera_height=camera_height,
                    num_frames=num_frames
                )
                
                # 使用 (轨迹类型, 高度) 作为键
                results[(traj_type, camera_height)] = (level, sequence_name)
                unreal.log(f"Successfully generated {traj_type} trajectory at {height_in_meters}m: {sequence_name}")
                
            except Exception as e:
                unreal.log_error(f"Failed to generate {traj_type} trajectory at {height_in_meters}m: {e}")
                # 继续生成其他轨迹类型和高度
                continue
    
    unreal.log(f"Generated {len(results)} trajectory sequences")
    return results

