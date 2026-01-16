# 并行渲染使用指南

## 快速开始

### 1. 基础用法 - 使用2个GPU并行渲染所有地图

```bash
cd /home_ssd/sjy/UE5_Project/PCGBiomeForestPoplar/Plugins/MatrixCityPlugin/misc
python run_parallel.py --num_gpus 2
```

### 2. 指定特定的GPU

```bash
# 使用GPU 0和GPU 1
python run_parallel.py --num_gpus 2 --gpu_ids 0,1

# 使用GPU 1和GPU 2（如果你有3个GPU）
python run_parallel.py --num_gpus 2 --gpu_ids 1,2
```

### 3. 只渲染特定地图

```bash
# 只渲染3个地图
python run_parallel.py --num_gpus 2 --maps scene_001 scene_002 scene_003

# 只渲染单个地图（但还是会用2个GPU准备）
python run_parallel.py --num_gpus 1 --maps scene_005
```

### 4. 使用自定义配置文件

```bash
python run_parallel.py --config custom_config.json --num_gpus 2
```

## 配置文件修改（可选）

在 `user.json` 中可以添加地图列表：

```json
{
  "ue_command": "...",
  "ue_project": "...",
  "maps": [
    "scene_001",
    "scene_002",
    "scene_003",
    "scene_004"
  ]
}
```

如果不配置，会自动扫描 `Content/Map/scene_*.umap` 文件。

## 工作原理

1. **任务分配**: 将所有地图均分给N个Worker（N=GPU数量）
2. **GPU隔离**: 每个Worker使用独立的GPU（通过CUDA_VISIBLE_DEVICES）
3. **端口隔离**: 每个Worker使用不同的Socket端口（9999, 10000, 10001...）
4. **进程隔离**: 每个Worker是独立的进程，互不影响

## 示例输出

```
2026-01-16 10:00:00 [INFO] 并行渲染配置:
2026-01-16 10:00:00 [INFO]   GPU数量: 2
2026-01-16 10:00:00 [INFO]   GPU IDs: [0, 1]
2026-01-16 10:00:00 [INFO]   总地图数: 9
2026-01-16 10:00:00 [INFO]   起始端口: 9999

2026-01-16 10:00:00 [INFO] Worker 0 (GPU 0): 5 个地图 - ['scene_001', 'scene_003', 'scene_005', 'scene_007', 'scene_009']
2026-01-16 10:00:00 [INFO] Worker 1 (GPU 1): 4 个地图 - ['scene_002', 'scene_004', 'scene_006', 'scene_008']
```

## 故障排查

### 如果端口被占用

```bash
# 修改起始端口
python run_parallel.py --num_gpus 2 --base_port 10000
```

### 如果GPU不够用

```bash
# 减少GPU数量
python run_parallel.py --num_gpus 1
```

### 查看GPU使用情况

```bash
# 实时监控GPU
watch -n 1 nvidia-smi
```

## 性能对比

- **单GPU串行**: 9个地图 × 30分钟 = 4.5小时
- **双GPU并行**: max(5个地图, 4个地图) × 30分钟 = 2.5小时
- **加速比**: ~1.8x
