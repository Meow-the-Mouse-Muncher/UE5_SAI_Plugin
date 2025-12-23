# UE5 序列轨迹导出到 3DGS 使用指南

本指南介绍如何使用两个核心脚本将 UE5 Level Sequence 轨迹导出为 3D Gaussian Splatting 训练数据格式。

## 脚本概述

### 1. `ue5_export_sequences_to_fbx.py`
**功能**：在 UE5 Editor 中批量导出 Level Sequence 为 FBX 文件
**运行环境**：UE5 Editor Python 控制台

### 2. `blender_extract_transforms.py`
**功能**：在 Blender 中处理 FBX 文件，提取相机轨迹并生成 3DGS 格式 JSON
**运行环境**：Blender 命令行模式

## 详细使用方法

### 步骤 1：UE5 导出 FBX 文件

#### 前置条件
- UE5 Editor 已打开项目 `PCGBiomeForestPoplar`
- 已加载目标地图（如 `scene_001`）
- Python 插件已启用

#### 配置脚本
在运行前，可以修改脚本中的地图名称：
```python
# 在 batch_export_sequences() 函数中修改这一行
MAP_NAME = "scene_001"  # 改为你的目标地图名称
```

#### 运行方法
1. **打开 Python 控制台**：
   - 菜单：`Window > Developer Tools > Python Console`

2. **执行脚本**：
   ```python
   exec(open(r'/home_ssd/sjy/UE5_Project/PCGBiomeForestPoplar/scripts/ue5_export_sequences_to_fbx.py').read())
   ```

#### 输出结果
脚本会在项目根目录创建 `Exported_FBX` 文件夹，按类别组织：
```
Exported_FBX/
├── fix_line/
│   ├── scene_001_Target_001_30.fbx
│   ├── scene_001_Target_001_50.fbx
│   └── scene_001_Target_001_80.fbx
├── rot_arc/
│   └── ...
└── rot_line/
    └── ...
```

#### 脚本功能详解
- **自动发现序列**：扫描 `/Game/Sequences` 下的所有 Level Sequence
- **地图过滤**：只导出包含指定地图名称的序列
- **分类导出**：按轨迹类型（fix_line, rot_arc, rot_line）分别存放
- **批量处理**：一次性处理所有符合条件的序列

### 步骤 2：Blender 处理 FBX 文件

#### 前置条件
- 已安装 Blender（建议 3.0+）
- 已完成步骤 1 的 FBX 导出
- 系统已安装 Python 包：`numpy`, `pyyaml`

#### 安装依赖
```bash
# 如果 Blender 的 Python 缺少依赖包
pip install numpy pyyaml
```

#### 运行方法
```bash
cd /home_ssd/sjy/UE5_Project/PCGBiomeForestPoplar
blender --background --python scripts/blender_extract_transforms.py
```

#### 输出结果
脚本会创建 `3dgs_data` 文件夹，包含完整的 3DGS 训练数据：
```
3dgs_data/
├── fix_line/
│   ├── scene_001_Target_001_30/
│   │   ├── transforms_raw.json      # 原始轨迹数据
│   │   └── pose/
│   │       └── transforms.json     # 3DGS 格式数据
│   └── scene_001_Target_001_50/
│       ├── transforms_raw.json
│       └── pose/
│           └── transforms.json
├── rot_arc/
└── rot_line/
```

#### 脚本功能详解
- **配置文件读取**：自动读取 MatrixCityPlugin 配置文件获取分辨率、相机名称等参数
- **FBX 导入**：批量导入所有 FBX 文件
- **相机识别**：智能查找指定名称的相机（CineCameraActor1）
- **轨迹提取**：逐帧提取相机的世界变换矩阵
- **格式转换**：应用 3DGS 变换逻辑生成最终 JSON 格式

## 配置文件说明

脚本会自动读取以下配置文件：

### `Plugins/MatrixCityPlugin/misc/user.json`
```json
{
  "ue_map": "/Game/Map/scene_001",
  "camera_name": "CineCameraActor1",
  ...
}
```

### `Plugins/MatrixCityPlugin/misc/render_SAI_config.yaml`
```yaml
Resolution: [1024, 1024]
Batch_Settings:
  camera_name: "CineCameraActor1"
...
```

## 生成的 JSON 格式

### `transforms_raw.json`（原始格式）
```json
{
    "camera_angle_x": 0.8575560450553894,
    "frames": [
        {
            "frame_index": 0,
            "rot_mat": [
                [1.0, 0.0, 0.0, 0.0],
                [0.0, 1.0, 0.0, 0.0],
                [0.0, 0.0, 1.0, 0.0],
                [0.0, 0.0, 0.0, 1.0]
            ]
        }
    ]
}
```

### `pose/transforms.json`（3DGS 格式）
```json
{
  "camera_angle_x": 0.8575560450553894,
  "fl_x": 598.0773,
  "fl_y": 598.0773,
  "k1": 0, "k2": 0, "k3": 0, "k4": 0,
  "p1": 0, "p2": 0,
  "cx": 512.0, "cy": 512.0,
  "w": 1024.0, "h": 1024.0,
  "frames": [
    {
      "file_path": "../rgb/0000.png",
      "transform_matrix": [
        [1.0, 0.0, 0.0, 0.0],
        [0.0, 1.0, 0.0, 0.0],
        [0.0, 0.0, 1.0, 0.0],
        [0.0, 0.0, 0.0, 1.0]
      ]
    }
  ]
}
```

## 自定义配置

### 修改地图名称
在 `ue5_export_sequences_to_fbx.py` 中：
```python
MAP_NAME = "your_map_name"  # 修改为你的地图名称
```

### 修改缩放因子
在 `blender_extract_transforms.py` 的 `main()` 函数中：
```python
scale_factor = 100  # 修改缩放因子
```

### 修改分辨率
在 `render_SAI_config.yaml` 中：
```yaml
Resolution: [1920, 1080]  # 修改为目标分辨率
```

## 故障排除

### UE5 相关问题
1. **"No Level Sequences found"**
   - 检查序列文件是否在 `/Game/Sequences` 路径下
   - 确认地图名称过滤是否正确

2. **导出失败**
   - 确保 UE5 Python 插件已启用
   - 检查磁盘空间是否充足

### Blender 相关问题
1. **"FBX directory not found"**
   - 确保先运行了 UE5 导出脚本
   - 检查 `Exported_FBX` 目录是否存在

2. **"No camera found"**
   - 检查 FBX 文件是否包含相机对象
   - 确认相机名称配置是否正确

3. **依赖包错误**
   ```bash
   # 安装缺失的包
   pip install numpy pyyaml
   ```

## 完整工作流程示例

```bash
# 1. 在 UE5 中导出 FBX（手动执行）
# 打开 UE5 Python 控制台，运行：
# exec(open(r'/home_ssd/sjy/UE5_Project/PCGBiomeForestPoplar/scripts/ue5_export_sequences_to_fbx.py').read())

# 2. 使用 Blender 处理 FBX
cd /home_ssd/sjy/UE5_Project/PCGBiomeForestPoplar
blender --background --python scripts/blender_extract_transforms.py

# 3. 检查输出结果
ls -la 3dgs_data/
```

## 输出文件用途

生成的 `pose/transforms.json` 文件可以直接用于：
- 3D Gaussian Splatting 训练
- NeRF 训练
- 其他基于相机轨迹的 3D 重建任务

文件格式完全兼容主流的 3D 重建框架。