import ast
import asyncio
import datetime
import time
import json
import socket
import subprocess
import sys
import logging
from pathlib import Path
from typing import List, Optional

import colorama
import psutil

from config import CfgNode

p = None
output_path = None
unreal_loaded = False


def setup_logging(log_path: Path):
    logging.basicConfig(
        level=logging.INFO, 
        format='[%(asctime)s] - %(levelname)s - %(message)s',
        handlers=[
            logging.FileHandler(str(log_path)),
            logging.StreamHandler()
        ]
    )

    formatter = logging.Formatter('[%(asctime)s] - %(levelname)s - %(message)s')
    ch = logging.StreamHandler()
    ch.setLevel(level=logging.DEBUG)
    ch.setFormatter(formatter)

    logging.getLogger("asyncio").setLevel(logging.INFO)
    logging.getLogger("asyncio").addHandler(ch)

    logging.info(f'Python Logging to {log_path.as_uri()}')


def format_time(seconds: float) -> str:
    return time.strftime("%Hh %Mm %Ss", time.gmtime(seconds))


def calculate_remaining_time(i_current, n_total, time_log: List[float]) -> str:
    remaining_time = 'N/A'
    if len(time_log) > 0:
        avg_time = sum(time_log) / len(time_log)
        remaining_time = (n_total - i_current) * avg_time
        remaining_time = format_time(remaining_time)
    return remaining_time


def ask_exit():
    for task in asyncio.all_tasks():
        task.cancel()


async def handle_client(client, host='127.0.0.1', port=9999, parent_loop=None):
    global unreal_loaded
    loop__ = asyncio.get_event_loop()
    while True:
        try:
            data_size = await loop__.sock_recv(client, 4)  # 4 byte (int32) size prefix on the message (ue defined)
            data_size = int.from_bytes(data_size, byteorder='little')
            data = (await loop__.sock_recv(client, data_size)).decode()
        except OSError:
            if parent_loop:
                # logging.info('Error Occurred. Restarting Unreal Engine & Socket Connection...\n')
                server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                server.bind((host, port))
                server.listen(8)
                server.setblocking(False)
                client, _ = await parent_loop.sock_accept(server)
                logging.info(f'Socket Connection from {client.getpeername()}')

        if not data:
            break
            
        logging.info(data)

        if data == '[*] Unreal Engine Loaded!':
            unreal_loaded = True

        # handle error
        if 'error' in data.lower():
            if p:
                logging.error('Unreal Engine Exit with Error. Killing Unreal Engine...')
                p.kill()
        
        # handle exit
        if data == 'Render completed. Success: True' or data == 'Pipeline Finished' or 'exit' in data.lower():
            if p:
                logging.info('Exiting Unreal Engine...')
                p.kill()
            break

        # await loop.sock_sendall(client, data.encode('utf8'))
    client.close()
    ask_exit()


async def run_server(host, port):
    server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server.bind((host, port))
    server.listen(8)
    server.setblocking(False)

    loop_ = asyncio.get_event_loop()

    # while True:
    client, _ = await loop_.sock_accept(server)
    logging.info(f'Socket Connection from {client.getpeername()}')
    loop_.create_task(handle_client(client, host, port, loop_))


async def run_cmd(command):
    global p, unreal_loaded
    unreal_loaded = False
    # p = subprocess.Popen(command, stdout=subprocess.DEVNULL, stderr=subprocess.STDOUT)
    # On Linux, use shell=True to handle the command string with arguments
    p = subprocess.Popen(command, shell=True)
    logging.info('[*] Starting Unreal Engine...')
    logging.info(f'[*] Unreal Engine PID: {p.pid}')

    if sys.version_info[1] > 8:
        AsyncioCancelledError = asyncio.exceptions.CancelledError
    else:
        AsyncioCancelledError = asyncio.CancelledError

    while True:
        try:
            await asyncio.sleep(10)
            poll = p.poll()

            crashed = False
            # Check for crash reporter process
            for proc in psutil.process_iter(['pid', 'name']):
                try:
                    # Linux crash reporter name might vary, usually CrashReportClient
                    if proc.info['name'] and 'CrashReportClient' in proc.info['name']:
                        logging.info(f"Found CrashReportClient (PID: {proc.info['pid']}), killing it...")
                        proc.kill()
                        crashed = True
                except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
                    pass

            if poll is not None or crashed:
                if p.poll() is None:
                    p.kill()
                logging.error(f'[!] Unreal Engine crashed/exited with poll code {poll}')
                logging.info('------------------')
                logging.info('[*] Restarting Unreal Engine...')
                unreal_loaded = False
                # p = subprocess.Popen(command, stdout=subprocess.DEVNULL, stderr=subprocess.STDOUT)
                p = subprocess.Popen(command, shell=True)
                logging.info(f'[*] Unreal Engine PID: {p.pid}')
        except AsyncioCancelledError as e:
            break
            # continue
        # logging.info('running', poll)


def main(config_file: str='misc/user.json'):
    colorama.init(autoreset=True)

    config_file = Path(config_file).resolve()
    with open(config_file) as f:
        config = json.load(f)
    ue_command = config['ue_command']
    ue_project = config['ue_project']
    # 新增：读取地图路径，如果没有则默认为空字符串
    ue_map = config.get('ue_map', '')

    global output_path
    render_config_path = Path(config['render_config']).resolve()
    render_config = CfgNode.load_yaml_with_base(str(render_config_path))
    output_path = Path(render_config['Output_Path']).resolve()
    python_log_file = output_path / f'_config/log_{datetime.datetime.now().strftime("%m-%d_%H-%M-%S")}.log'
    python_log_file.parent.mkdir(parents=True, exist_ok=True)
    setup_logging(python_log_file)

    # if ' ' in ue_project:
    #     raise ValueError(f"Found blanks in `ue_project` path. UE can't handle that. `ue_project`: {ue_project}")

    # python_dir = Path(ue_project).parent / 'Plugins/MatrixCityPlugin/Content/Python'
    # python_script = python_dir / 'pipeline.py'
    python_script = Path(config['python_script']).resolve() # 运行的python脚本路径


    script_path_str = str(python_script).replace('\\', '/')
    print(f'script_path_str: {script_path_str}')
    
    command=[
        f'"{ue_command}"',
        f'"{ue_project}"',
        f'"{ue_map}"' if ue_map else '',  # 加载指定地图
        f'-ExecCmds="py {script_path_str}"',
        f'-render_config_path="{render_config_path}"',
        f'-target_map="{ue_map}"' if ue_map else '',  # 传递地图路径参数
        
        f'-notexturestreaming',
        # f'-silent',
        f'LOG=Pipeline.log',
        f'-LOCALLOGTIMES',
    ]
    command = ' '.join(map(str, command))
    logging.info(colorama.Fore.BLUE + command)

    ue_log_file = Path(ue_project).parent / "Saved/Logs/Pipeline.log"
    logging.info(colorama.Fore.YELLOW + f'[*] UE log file: {ue_log_file.as_uri()}')

    host = '127.0.0.1'
    port = 9999

    loop = asyncio.get_event_loop()
    try:
        print('now we need cmd pipeline')
        loop.create_task(run_server(host, port))
        loop.run_until_complete(run_cmd(command))

        # asyncio.ensure_future(run_server(host, port)),
        # asyncio.ensure_future(run_cmd(command))
        # loop.run_forever()

    except KeyboardInterrupt:
        global p
        if p:
            p.kill()
            logging.info('[*] Unreal Engine is killed by keyboard interrupt.')
        pass

    else:
        # logging.info("Pipeline Finished!")
        loop.close()
        logging.info(f'output_path: {output_path.as_uri()}')


        # TODO: add post-processing
        # logging.info('Doing some post-processing...')


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description='main')
    parser.add_argument('--config_file', '-f', type=str, default='misc/user.json')
    args = parser.parse_args()

    main(args.config_file)
