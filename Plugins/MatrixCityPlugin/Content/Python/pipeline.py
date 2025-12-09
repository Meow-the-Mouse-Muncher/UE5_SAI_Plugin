import unreal
import utils_sequencer  # 轨迹脚本

from GLOBAL_VARS import PLUGIN_ROOT
from data.config import CfgNode
from utils import loader_func, log_msg_with_socket
from custom_movie_pipeline import CustomMoviePipeline # 导入自定义的渲染管线类

unreal.log_warning("######################################################")
unreal.log_warning("### PIPELINE.PY HAS STARTED EXECUTION ###")
unreal.log_warning("######################################################")

@loader_func
def main(render_config_path: str = str(PLUGIN_ROOT / 'misc/render_config_common.yaml')):

    # 1. connect to socket
    host = '127.0.0.1'
    port = 9999
    PIEExecutor = unreal.MoviePipelinePIEExecutor()
    PIEExecutor.connect_socket(host, port)
    log_msg_with_socket(PIEExecutor, '[*] Unreal Engine Loaded!')

    # 2. create a sequence  
    level, sequence_name = utils_sequencer.main() # 调用 utils_sequencer 生成相机轨迹
    log_msg_with_socket(PIEExecutor, f'[*] Created Sequence: {sequence_name}')

    # 3. render the sequence
    unreal.log_warning(f"Render Config Path: {render_config_path}")
    render_config = CfgNode.load_yaml_with_base(render_config_path) # 读取 YAML 渲染配置文件
    CustomMoviePipeline.clear_queue()
    # 将任务添加到队列中
    CustomMoviePipeline.add_job_to_queue_with_render_config(
        level=level,
        level_sequence=sequence_name,
        render_config=render_config
    )
    # 开始渲染！
    CustomMoviePipeline.render_queue(executor=PIEExecutor) # 原来的调用


if __name__ == "__main__":
    (cmdTokens, cmdSwitches, cmdParameters) = unreal.SystemLibrary.parse_command_line(
        unreal.SystemLibrary.get_command_line()
    )

    try:
        render_config_path = cmdParameters['render_config_path']
    except:
        # raise Exception('Please specify config file')
        render_config_path = None
        print('No config file specified, using default config file')

    main(render_config_path)