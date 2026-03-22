# UE5 to 3DGS/NeRF Data Pipeline (PCGBiomeForestPoplar)

## 简介 (Introduction)

本仓库提供了一套完整的数据处理流水线（Pipeline）工具集，用于从 Unreal Engine 5 (UE5) 项目中导出摄像机轨迹，并转换为可用于训练 **3D Gaussian Splatting (3DGS)** 或 NeRF 等神经渲染模型的标准数据集格式。

> **声明 / Acknowledgement**: 本项目基于 [MatrixCity](https://github.com/city-super/MatrixCity/tree/main) 仓库的流程与插件进行构建和定制化扩展。特别感谢原项目提供的基础导出架构。

**请注意**：本仓库**仅包含数据导出插件和后处理脚本**，并不包含 UE5 项目本身的资产（如场景模型、PCG 生态系统等）。它是作为一个工具集集成到现有的 UE5 环境（如 PCGBiomeForestPoplar 项目）中使用的。

## 主要功能 (Key Features)

- **UE5 序列导出插件**：包含了修改版的 `MatrixCityPlugin`，提供了在 UE5 内控制和导出数据的底层能力。
- **批量轨迹数据导出**：通过 Python 脚本（`scripts/ue5_export_sequences_to_fbx.py`）在 UE5 Editor 中批量将 Level Sequence 的相机运动轨迹导出为 FBX 文件。
- **3DGS 数据集生成**：利用 Blender 命令行模式（`scripts/blender_extract_transforms.py`）读取导出的 FBX 文件，提取相机的世界变换矩阵，并自动生成 3DGS 训练所需的 `transforms.json` 文件。
- **位姿对齐与精调**：提供一系列后处理脚本（如 `align_pose_to_center_*.py`），用于调整和对齐相机位姿，使其更适合后续 3DGS/NeRF 模型的训练要求。

## 仓库结构 (Repository Structure)

```text
├── Plugins/
│   └── MatrixCityPlugin/      # 用于 UE5 内数据渲染和导出的核心插件
├── scripts/                   # 包含所有工作流自动化和数据后处理的 Python 脚本
│   ├── ue5_export_sequences_to_fbx.py      # (UE5 环境运行) 批量导出动画序列到 FBX
│   ├── blender_extract_transforms.py       # (Blender 环境运行) 从 FBX 提取位姿并生成 transforms.json
│   ├── align_pose_to_center_*.py           # 用于对齐和中心化相机位姿的后处理脚本
│   └── Trans_3Dgs_Data.md                  # 详细的脚本使用指南与说明
└── .gitignore                 # Git 忽略配置
```

## 工作流程 (Workflow Overview)

完整的 3DGS 数据导出工作流程主要包含以下步骤（详细图文指南请参阅 [`scripts/Trans_3Dgs_Data.md`](scripts/Trans_3Dgs_Data.md)）：

1. **配置环境**：
   将本仓库克隆或解压至你的 UE5 项目（例如 `PCGBiomeForestPoplar`）根目录下，确保 `Plugins/` 和 `scripts/` 处于项目根级。

2. **在 UE5 中导出 FBX 轨迹**：
   打开你的 UE5 项目，在 Python 控制台中执行 `ue5_export_sequences_to_fbx.py` 脚本，将指定地图的 Level Sequence 批量导出到 `Exported_FBX/` 目录下。

3. **使用 Blender 提取并转换位姿**：
   在系统命令行中使用 Blender 的后台模式执行 `blender_extract_transforms.py`，它会读取上一步生成的 FBX 文件，计算相机的内外参，并生成标准的 `transforms.json` 格式数据。
   ```bash
   blender --background --python scripts/blender_extract_transforms.py
   ```

4. **数据后处理（可选）**：
   使用 `scripts/` 目录下的其他对齐工具（如 `align_pose_to_center_inv_h5.py` 等）对生成的位姿进行中心化对齐、深度图可视化等进一步处理，以满足特定的训练需求。

## 运行环境要求

- **Unreal Engine 5** (需启用 Python Editor Script Plugin，以运行导出脚本和 MatrixCity 插件)
- **Blender** 3.0+ (用于运行无头位姿提取脚本)
- **Python 3.x** (系统环境，依赖包：`numpy`, `pyyaml`, `h5py` 等)
