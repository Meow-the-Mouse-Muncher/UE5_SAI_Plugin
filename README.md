# UE5 SAI Plugin

从 UE5.6 森林场景生成 OCC/GT 配对 RGB、深度、FBX 和相机 JSON，支持按已批准序列计划分配 GPU、断点恢复和输出校验。

本项目基于 [MatrixCity](https://github.com/city-super/MatrixCity) 的插件与导出流程开发，保留原有署名。插件的模块名和资产路径仍为 `MatrixCityPlugin`，以保持 UE 资产引用有效。

## 仓库范围

仓库包含插件 C++/Python 源码、插件蓝图和材质、项目构建源码、UE 配置、数据集清单及 CPU 测试。森林地图、模型、纹理等项目 `Content/` 素材需单独部署；克隆代码不能直接得到完整森林工程。

| 路径 | 用途 |
| --- | --- |
| `Plugins/MatrixCityPlugin/Source/` | MRQ 输出和渲染同步的 C++ 实现 |
| `Plugins/MatrixCityPlugin/Content/` | UE 内 Python、配套蓝图与材质 |
| `Plugins/MatrixCityPlugin/misc/` | 系统 Python 调度器、GPU 分配、配置与恢复逻辑 |
| `Source/`、`.uproject`、`Config/DefaultEngine.ini` | 项目模块的构建入口和渲染设置 |
| `Config/Dataset/` | 已批准的地图、素材哈希与 train/test 序列清单 |
| `scripts/render_forest_dataset.py` | train → test 的持久任务入口 |
| `scripts/export_camera_json.py` | 已有 RGB/深度/FBX 的独立相机元数据导出 |
| `tests/`、`scripts/forest_dataset/test_*.py`、插件 `tests/` | CPU 回归测试 |

## 安装和配置

系统 Python 需 3.10 或以上。调度器和 CPU 测试依赖可通过以下命令安装；UE 内置 Python 的部署版本单独记录在 [requirements-ue-linux.txt](scripts/forest_dataset/requirements-ue-linux.txt)。Blender 使用自己的 Python。

```bash
python3 -m pip install -r requirements-dev.txt
```

将代码放在对应森林工程根目录，并单独部署 `Content/` 素材。插件和项目二进制由本机 UE 构建，不进入 Git。在 Linux 上可使用对应 UE5.6 安装目录构建：

```bash
UE_ROOT=/path/to/UE_5.6
"$UE_ROOT/Engine/Build/BatchFiles/Linux/Build.sh" \
  PCGBiomeForestPoplarEditor Linux Development \
  "$PWD/PCGBiomeForestPoplar.uproject" -WaitMutex
```

正式采集使用的 UE5.6.1 引擎修复、构建记录与运行验收见 [FOREST_DATASET.md](FOREST_DATASET.md#vulkan修复与重新采集) 和 `scripts/engine_patches/`。

首次部署时复制配置模板；已有本机配置应直接保留：

```bash
cp -n Plugins/MatrixCityPlugin/misc/user.example.json \
  Plugins/MatrixCityPlugin/misc/user.json
```

将 `ue_command` 改为本机编辑器或启动包装器的绝对路径，并核对 `ue_project`、地图列表和输出目录。模板的项目路径相对于配置目录，插件脚本路径可相对于插件根目录。模板包括全部 28 张正式地图。

`user.json`、`user.local*.json` 和可选的 `email.json` 留在本地；自定义配置也可以通过 `--config /absolute/config.json` 传入。Linux 部署可按需要配置 `run_as_user`、`environment` 和 `unset_environment`，含义见 [配置与执行流程](Plugins/MatrixCityPlugin/docs/DATA_GENERATION_PIPELINE.md)。

## 运行

先验证一张测试地图，不启动 UE、Blender 或 GPU 探测：

```bash
python3 Plugins/MatrixCityPlugin/misc/render_dataset.py \
  --split test --maps /Game/Map/dataset/test/scene_010_grass \
  --gpu-ids 0 --workers-per-gpu 1 --dry-run
```

部署完整素材并通过小规模渲染验收后，新任务使用新的运行目录和输出目录：

```bash
python3 scripts/render_forest_dataset.py \
  --run-dir Saved/RenderRuns/my_session \
  --output-root /absolute/dataset \
  --gpu-ids auto --dry-run
```

确认计划后移除 `--dry-run` 开始执行；恢复已有任务时使用相同目录并加 `--resume`。`scripts/render_forest_dataset.sh` 保留当前服务器已有 session 的续跑默认值；新部署优先使用上面的 Python 入口，或显式设置 `FOREST_CONFIG`、`FOREST_RUN_DIR` 和 `FOREST_OUTPUT_ROOT`。

相机 JSON 单独补导出的方法见 [相机协议与导出](docs/CAMERA_JSON_EXPORT.md)。正式采集计划、OCC/GT 定义及服务器恢复说明见 [森林数据集](FOREST_DATASET.md)。

## 开发和维护

在仓库根目录执行：

```bash
python3 -m pytest -q
```

这些测试使用 CPU 和模拟 UE 接口；真实场景渲染、GPU 驱动与引擎补丁仍需在部署环境验收。

[仓库维护清单](docs/REPOSITORY_MAINTENANCE.md) 说明当前维护范围、部署文件和已删除的历史工具。相机元数据统一使用当前 OpenCV 协议。
