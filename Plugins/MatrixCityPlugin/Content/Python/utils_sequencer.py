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

def generate_train_box(line1, line2, z, current_frame):
    # [0, 0, -80000.0, 0], 
    # [0, 38000, -80000.0, 38000]
    x11, y11, x12, y12=line1
    x21, y21, x22, y22=line2
    assert(y11==y12)
    assert(y21==y22)
    assert(x11==x21)
    assert(x12==x22)
    w=math.dist([x11, y11], [x12, y12])
    h=math.dist([x11, y11], [x21, y21])
    interval=800 # train interval 采样间隔
    w_instance=int(w/interval)+2
    h_instance=int(h/interval)+1
    x_start=np.linspace(x11, x12, w_instance)
    y_start=np.linspace(y11, y12, w_instance)
    x_end=np.linspace(x21, x22, w_instance)
    y_end=np.linspace(y21, y22, w_instance)
    camera_trans=[]
    
    # train
    if z>25000:
        pitch=-60
    else:
        pitch=-45
    for i in range(w_instance):
        camera_trans.append( 
            SequenceKey(
            frame=current_frame, 
            location=(x_start[i], y_start[i], z),
            rotation=(0, pitch, 0)
            )
        )
        current_frame=current_frame+h_instance
        camera_trans.append( 
            SequenceKey(
            frame=current_frame, 
            location=(x_end[i], y_end[i], z),
            rotation=(0, pitch, 0)
            )
        )
        current_frame=current_frame+1
    for i in range(w_instance):
        camera_trans.append( 
            SequenceKey(
            frame=current_frame, 
            location=(x_start[i], y_start[i], z),
            rotation=(0, pitch, 90)
            )
        )
        current_frame=current_frame+h_instance
        camera_trans.append( 
            SequenceKey(
            frame=current_frame, 
            location=(x_end[i], y_end[i], z),
            rotation=(0, pitch, 90)
            )
        )
        current_frame=current_frame+1
    for i in range(w_instance):
        camera_trans.append( 
            SequenceKey(
            frame=current_frame, 
            location=(x_start[i], y_start[i], z),
            rotation=(0, pitch, 180)
            )
        )
        current_frame=current_frame+h_instance
        camera_trans.append( 
            SequenceKey(
            frame=current_frame, 
            location=(x_end[i], y_end[i], z),
            rotation=(0, pitch, 180)
            )
        )
        current_frame=current_frame+1
    for i in range(w_instance):
        camera_trans.append( 
            SequenceKey(
            frame=current_frame, 
            location=(x_start[i], y_start[i], z),
            rotation=(0, pitch, 270)
            )
        )
        current_frame=current_frame+h_instance
        camera_trans.append( 
            SequenceKey(
            frame=current_frame, 
            location=(x_end[i], y_end[i], z),
            rotation=(0, pitch, 270)
            )
        )
        current_frame=current_frame+1
    return camera_trans, current_frame


def main(target_actor=None, map_name=None):
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
    sequence_dir = '/Game/Sequences'
    seq_fps = 24
    current_frame=0
    
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
    
    # 1. create a new level sequence
    # 根据地图名和目标物名生成序列名称
    if map_name and target_actor:
        target_name = target_actor.get_name()
        sequence_name = f'{map_name}_{target_name}_sequence'
    else:
        sequence_name = 'aerial_train'  # 默认名称
        
    fov = 40
    
    # 如果有目标物，围绕目标物生成轨迹
    if target_actor:
        target_location = target_actor.get_actor_location()
        target_x, target_y, target_z = target_location.x, target_location.y, target_location.z
        
        # 围绕目标物生成一个盒子轨迹，可以根据需要调整参数
        box_size = 2000  # 盒子大小
        height = target_z + 1000  # 相机高度
        
        camera_trans, current_frame = generate_train_box(
            [target_x - box_size, target_y - box_size, target_x + box_size, target_y - box_size], 
            [target_x - box_size, target_y + box_size, target_x + box_size, target_y + box_size], 
            height, 
            current_frame
        )
    else:
        # 使用默认轨迹
        camera_trans, current_frame = generate_train_box(
            [33900, 38500, 40000, 38500], 
            [33900, 46500, 40000, 46500], 
            6500, 
            current_frame
        )
 

    seq_length=current_frame  #使用计算出的序列长度
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


if __name__ == "__main__":
    main()
