# 配置与执行流程

正式入口 `scripts/render_forest_dataset.py` 先运行 train，再运行 test；每个 split 使用 `misc/render_dataset.py` 调度独立 UE worker，并在图像与 FBX 完成后导出相机 JSON。

## 配置归属

| 配置 | 作用 |
| --- | --- |
| `misc/user.example.json` | 入库的部署模板；包括全部正式地图 |
| 本机 `misc/user.json` 或 `--config` 指定文件 | 编辑器、项目、地图、Python 入口和渲染配置路径 |
| `misc/render_SAI_config.yaml` | 正式分辨率、通道、采样和输出默认值；继承 `render_config.yaml` |
| `--render-config` | 单 split 调度器的显式渲染配置覆盖 |
| `misc/trajectory_config.yaml` | 轨迹参数、25 帧和相机名称 |
| `Config/Dataset/` | 正式素材哈希、目标、遮挡树和批准的 train/test 序列 |
| `Saved/RenderRuns/<session>/` | 本地任务计划、配置快照、完成标记和日志 |

系统配置的相对路径先按配置文件目录解析，找不到时按插件根目录解析。`pipeline.py` 尊重启动器传入的 `render_config_path`，未传入时使用 `misc/render_SAI_config.yaml`。

Linux 的可选 `run_as_user` 在应用运行环境前切换用户；`environment` 为字符串键值表，`unset_environment` 为待移除变量名列表。模板不预设服务器用户名、驱动路径或固定引擎包装器。

## 调度与渲染

```text
scripts/render_forest_dataset.py
  → misc/render_dataset.py（train / test）
    → misc/run_cmd_async.py（独立 GPU、端口、日志与 worker 配置）
      → Content/Python/pipeline.py
        → utils_sequencer.py / trajectory_geometry.py
        → custom_movie_pipeline.py / render_controller.py
        → export_sequences_to_fbx.py
    → scripts/export_camera_json.py
      → Blender / scripts/blender_extract_transforms.py
```

调度器校验地图和素材，按正式 CSV 分片，为 worker 分配互不重叠的任务。随机种子按任务身份派生，GPU 和 worker 调整不应改变轨迹。实际运行的渲染配置、轨迹配置与计划写入运行目录。

UE 按目标创建精确 Level Sequence。OCC 与 GT 复用相同轨迹；正式数据仅切换清单指定的原树，保留地形、草、石块与其他背景。其他互斥目标隐藏。默认两份均输出完整 25 帧 RGB 和深度。

每个 worker 使用隔离的 `/Game/Sequences/_workers/...` 目录，自动 FBX 导出接收本轮生成的精确序列列表。C++ `MatrixCityRenderSyncLibrary` 在推进 Python 渲染状态前执行原生同步；同步失败使任务失败。

## 恢复与停止

`--resume` 复用记录的计划和配置，拒绝不兼容的采样或清单变化。完整图像和非空 FBX 通过检查后可复用；相机 JSON 缺失或失效时补导出。`render_done.json` 标记图像/FBX 阶段，`done.json` 表示任务所需阶段全部完成。

同一 session 和输出目录有进程锁。SIGINT/SIGTERM 由调度器转给 worker，worker 通过 MRQ 取消和 PIE 退出完成清理。`result.json`、`progress.json`、`ue.log`、`launcher.log` 与 `camera_export.log` 用于定位任务状态。

具体运行命令见[快速开始](Get-Started.md)，已部署服务器的路径、引擎修复和恢复状态见[森林数据集](../../../FOREST_DATASET.md)。

## 相机元数据

`export_camera_json.py` 通过明确的任务清单调用 Blender。JSON 使用 OpenCV 相机到世界矩阵、米单位平移、固定 25 视角及原生 512×512 图像。GT/OCC 两份 JSON 必须一致。完整字段、验证边界和单独补导出命令见[相机协议](../../../docs/CAMERA_JSON_EXPORT.md)。

## 其他维护入口

`misc/run_parallel.py` 转发到当前调度器。`misc/run_cmd_async.py` 可用于单 worker 调试，`misc/benchmark_render.py` 用于独立性能采样。

FBX 由正式 worker 自动导出本轮精确序列，相机 JSON 可通过 `scripts/export_camera_json.py` 单独补导出。旧手工导出、重聚焦、可视化与历史文档已删除，维护范围见[仓库维护清单](../../../docs/REPOSITORY_MAINTENANCE.md)。
