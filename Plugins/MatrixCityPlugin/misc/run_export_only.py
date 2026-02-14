import ast
import asyncio
import datetime
import time
import json
import os
import socket
import subprocess
import sys
import logging
from pathlib import Path
from typing import List, Optional

import colorama
import psutil

from config import CfgNode

# Reuse logic from run_cmd_async.py where possible, but simplified for single-run export task

def main(config_file: str='misc/user.json', gpu_id: Optional[int]=None, map_filter: Optional[List[str]]=None):
    colorama.init(autoreset=True)
    logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

    config_file = Path(config_file).resolve()
    with open(config_file) as f:
        config = json.load(f)
    ue_command = config['ue_command']
    ue_project = config['ue_project']
    
    # Use the export pipeline script
    python_script = Path(ue_project).parent / 'Plugins/MatrixCityPlugin/Content/Python/pipeline_export.py'
    
    # Determine which maps to process
    # If map_filter is provided (command line args), use those.
    # Otherwise use ue_map or maps from config if present.
    maps_to_process = []
    if map_filter:
        maps_to_process = map_filter
    else:
        ue_map = config.get('ue_map', '')
        if ue_map:
            maps_to_process = [ue_map]
        else:
            # Fallback to 'maps' list
            maps_to_process = config.get('maps', [])
            
    # Normalize map paths (prepend /Game/Map/ if missing)
    for i in range(len(maps_to_process)):
        m = maps_to_process[i]
        if m and not m.startswith('/') and not m.startswith('Game'):
            maps_to_process[i] = f"/Game/Map/{m}"
            
    if not maps_to_process:
        logging.warning("No maps specified in config or arguments. UE will load default startup map.")
        # We run once with empty map arg, letting UE use default or current.
        maps_to_process = ['']

    script_path_str = str(python_script).replace('\\', '/')
    logging.info(f'Python script to execute: {script_path_str}')

    for map_path in maps_to_process:
        logging.info(f"Processing map: {map_path if map_path else 'Default'}")
        
        command = [
            f'"{ue_command}"',
            f'"{ue_project}"',
            f'"{map_path}"' if map_path else '',
            f'-ExecCmds="py {script_path_str}"',
            f'-target_map="{map_path}"' if map_path else '',
            '-notexturestreaming',
            'LOG=PipelineExport.log',
            '-LOCALLOGTIMES',
        ]
        
        if gpu_id is not None:
            command.append(f'-graphicsadapter={gpu_id}')

        full_command = ' '.join(map(str, command))
        logging.info(f"Executing command: {full_command}")

        env = dict(os.environ)
        if gpu_id is not None:
            env['CUDA_VISIBLE_DEVICES'] = str(gpu_id)

        # Run subprocess blocking (no need for async socket server here necessarily, 
        # unless we want real-time feedback, but simpler is better for this task)
        # pipeline_export.py quits editor at the end, so this process will terminate.
        try:
            process = subprocess.run(full_command, shell=True, env=env)
            if process.returncode != 0:
                logging.error(f"UE exited with error code {process.returncode} for map {map_path}")
            else:
                logging.info(f"Successfully processed map {map_path}")
        except KeyboardInterrupt:
            logging.info("Interrupted by user.")
            break

if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description='Run UE export pipeline')
    parser.add_argument('--config_file', '-f', type=str, default='misc/user.json')
    parser.add_argument('--gpu_id', '-g', type=int, default=None, help='GPU ID to use')
    parser.add_argument('--maps', '-m', type=str, nargs='*', default=None, help='Specific maps to process (e.g. /Game/Map/scene_010)')
    args = parser.parse_args()

    main(args.config_file, gpu_id=args.gpu_id, map_filter=args.maps)
