#!/usr/bin/env python3
"""
并行渲染调度器 - 支持多GPU并行处理多个地图

使用示例:
    # 使用2个GPU并行渲染所有地图
    python run_parallel.py --num_gpus 2
    
    # 指定GPU ID和配置文件
    python run_parallel.py --num_gpus 2 --gpu_ids 0,1 --config misc/user.json
    
    # 只渲染特定地图
    python run_parallel.py --num_gpus 2 --maps scene_001 scene_002 scene_003
"""

import argparse
import json
import logging
import multiprocessing as mp
import os
import sys
import time
from pathlib import Path
from typing import List, Optional

import colorama


def setup_logging():
    """配置日志"""
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s [%(levelname)s] %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S'
    )


def get_all_maps(config_file: Path) -> List[str]:
    """从配置或Content/Map目录扫描所有地图
    
    Args:
        config_file: 配置文件路径
        
    Returns:
        地图名称列表，例如 ['scene_001', 'scene_002', ...]
    """
    with open(config_file) as f:
        config = json.load(f)
    
    # 如果配置中有地图列表，使用配置
    if 'maps' in config and isinstance(config['maps'], list):
        maps = config['maps']
        logging.info(f"从配置文件读取到 {len(maps)} 个地图")
        return maps
    
    # 否则扫描 Content/Map 目录
    ue_project = Path(config['ue_project']).resolve()
    map_dir = ue_project.parent / 'Content' / 'Map'
    
    if not map_dir.exists():
        logging.warning(f"地图目录不存在: {map_dir}")
        return []
    
    # 扫描所有 .umap 文件
    map_files = list(map_dir.glob('scene_*.umap'))
    maps = [f.stem for f in map_files]  # 只取文件名，不要扩展名
    maps.sort()
    
    logging.info(f"从目录 {map_dir} 扫描到 {len(maps)} 个地图")
    return maps


def split_maps(maps: List[str], num_parts: int) -> List[List[str]]:
    """将地图列表均分成N份
    
    Args:
        maps: 地图列表
        num_parts: 分成几份
        
    Returns:
        分片后的列表，例如 [[map1, map2], [map3, map4], ...]
    """
    result = [[] for _ in range(num_parts)]
    for i, map_name in enumerate(maps):
        result[i % num_parts].append(map_name)
    return result


def run_worker(worker_id: int, maps: List[str], gpu_id: int, port: int, config_file: str):
    """Worker进程函数 - 处理分配给它的地图列表
    
    Args:
        worker_id: Worker编号
        maps: 要处理的地图列表
        gpu_id: 使用的GPU ID
        port: Socket端口
        config_file: 配置文件路径
    """
    import subprocess
    
    # 设置进程特定的日志
    logger = logging.getLogger(f'Worker-{worker_id}')
    
    logger.info(colorama.Fore.GREEN + f"=" * 60)
    logger.info(colorama.Fore.GREEN + f"Worker {worker_id} 启动")
    logger.info(colorama.Fore.GREEN + f"GPU: {gpu_id}, Port: {port}")
    logger.info(colorama.Fore.GREEN + f"分配的地图数: {len(maps)}")
    logger.info(colorama.Fore.GREEN + f"地图列表: {', '.join(maps)}")
    logger.info(colorama.Fore.GREEN + f"=" * 60)
    
    # 获取当前脚本所在目录
    script_dir = Path(__file__).parent
    run_cmd_script = script_dir / 'run_cmd_async.py'
    
    success_count = 0
    fail_count = 0
    
    for i, map_name in enumerate(maps, 1):
        logger.info(colorama.Fore.CYAN + f"\n[{i}/{len(maps)}] 开始处理地图: {map_name}")
        
        # 创建临时配置文件，指定当前地图
        temp_config = script_dir / f'temp_config_worker_{worker_id}.json'
        
        # 读取原始配置
        with open(config_file) as f:
            config = json.load(f)
        
        # 修改地图路径
        config['ue_map'] = f'/Game/Map/{map_name}'
        
        # 写入临时配置
        with open(temp_config, 'w') as f:
            json.dump(config, f, indent=2)
        
        # 构建命令
        cmd = [
            sys.executable,  # 当前Python解释器
            str(run_cmd_script),
            '--config_file', str(temp_config),
            '--gpu_id', str(gpu_id),
            '--port', str(port)
        ]
        
        logger.info(f"执行命令: {' '.join(cmd)}")
        
        try:
            # 设置环境变量，指定GPU
            env = os.environ.copy()
            env['CUDA_VISIBLE_DEVICES'] = str(gpu_id)
            logger.info(f"设置 CUDA_VISIBLE_DEVICES={gpu_id}")
            
            # 运行子进程
            start_time = time.time()
            result = subprocess.run(cmd, check=True, capture_output=False, env=env)
            elapsed = time.time() - start_time
            
            logger.info(colorama.Fore.GREEN + f"✓ 地图 {map_name} 处理完成，耗时 {elapsed:.1f}s")
            success_count += 1
            
        except subprocess.CalledProcessError as e:
            logger.error(colorama.Fore.RED + f"✗ 地图 {map_name} 处理失败: {e}")
            fail_count += 1
            
        finally:
            # 清理临时配置文件
            if temp_config.exists():
                temp_config.unlink()
    
    logger.info(colorama.Fore.GREEN + f"\n" + "=" * 60)
    logger.info(colorama.Fore.GREEN + f"Worker {worker_id} 完成")
    logger.info(colorama.Fore.GREEN + f"成功: {success_count}, 失败: {fail_count}")
    logger.info(colorama.Fore.GREEN + f"=" * 60)


def main():
    parser = argparse.ArgumentParser(
        description='并行渲染调度器 - 使用多个GPU并行处理多个地图',
        formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument('--config', '-c', type=str, default='misc/user.json',
                        help='配置文件路径 (默认: misc/user.json)')
    parser.add_argument('--num_gpus', '-n', type=int, default=2,
                        help='使用的GPU数量 (默认: 2)')
    parser.add_argument('--gpu_ids', type=str, default=None,
                        help='指定GPU ID，用逗号分隔 (例如: 0,1)。不指定则使用0到num_gpus-1')
    parser.add_argument('--base_port', type=int, default=9999,
                        help='起始端口号，每个worker使用base_port+i (默认: 9999)')
    parser.add_argument('--maps', '-m', type=str, nargs='*', default=None,
                        help='要处理的地图列表。不指定则处理所有地图')
    
    args = parser.parse_args()
    
    # 初始化
    colorama.init(autoreset=True)
    setup_logging()
    
    config_file = Path(args.config).resolve()
    if not config_file.exists():
        logging.error(f"配置文件不存在: {config_file}")
        sys.exit(1)
    
    # 获取地图列表
    if args.maps:
        maps = args.maps
        logging.info(f"使用指定的 {len(maps)} 个地图")
    else:
        maps = get_all_maps(config_file)
    
    if not maps:
        logging.error("没有找到要处理的地图")
        sys.exit(1)
    
    # 解析GPU ID
    if args.gpu_ids:
        gpu_ids = [int(x.strip()) for x in args.gpu_ids.split(',')]
        if len(gpu_ids) != args.num_gpus:
            logging.warning(f"指定的GPU数量 ({len(gpu_ids)}) 与 --num_gpus ({args.num_gpus}) 不符，使用实际GPU列表")
            args.num_gpus = len(gpu_ids)
    else:
        gpu_ids = list(range(args.num_gpus))
    
    logging.info(colorama.Fore.YELLOW + "\n" + "=" * 70)
    logging.info(colorama.Fore.YELLOW + "并行渲染配置:")
    logging.info(colorama.Fore.YELLOW + f"  GPU数量: {args.num_gpus}")
    logging.info(colorama.Fore.YELLOW + f"  GPU IDs: {gpu_ids}")
    logging.info(colorama.Fore.YELLOW + f"  总地图数: {len(maps)}")
    logging.info(colorama.Fore.YELLOW + f"  起始端口: {args.base_port}")
    logging.info(colorama.Fore.YELLOW + "=" * 70 + "\n")
    
    # 分配地图
    map_partitions = split_maps(maps, args.num_gpus)
    
    for i, partition in enumerate(map_partitions):
        logging.info(f"Worker {i} (GPU {gpu_ids[i]}): {len(partition)} 个地图 - {partition}")
    
    # 创建进程池
    logging.info(colorama.Fore.CYAN + f"\n启动 {args.num_gpus} 个并行Worker...\n")
    
    processes = []
    for i in range(args.num_gpus):
        port = args.base_port + i
        p = mp.Process(
            target=run_worker,
            args=(i, map_partitions[i], gpu_ids[i], port, str(config_file))
        )
        p.start()
        processes.append(p)
        logging.info(f"Worker {i} 已启动 (PID: {p.pid})")
    
    # 等待所有进程完成
    logging.info(colorama.Fore.CYAN + "\n等待所有Worker完成...\n")
    
    for i, p in enumerate(processes):
        p.join()
        logging.info(f"Worker {i} 已完成")
    
    logging.info(colorama.Fore.GREEN + "\n" + "=" * 70)
    logging.info(colorama.Fore.GREEN + "所有渲染任务完成!")
    logging.info(colorama.Fore.GREEN + "=" * 70)


if __name__ == '__main__':
    main()
