# 快速开始

本插件用于 UE5.6 森林数据采集。先按[仓库安装说明](../../../README.md#安装和配置)部署素材、构建插件并创建本机 `misc/user.json`。

## 检查场景和清单

正式地图位于 `/Game/Map/dataset/{train,test}/`。目标 Actor 标签以 `Target_` 开头，相机标签默认 `CineCameraActor1`；正式 OCC/GT 按 `Config/Dataset/dataset_manifest.json` 中的树木内部名称切换。通用场景回退使用 `SM_` 标签前缀。

正式训练/测试计划分别位于 `Config/Dataset/train_sequences.csv` 和 `test_sequences.csv`。调度器按清单选择组合，不重新展开完整笛卡尔积。

## 验证单图计划

从项目根目录执行：

```bash
python3 Plugins/MatrixCityPlugin/misc/render_dataset.py \
  --split test --maps /Game/Map/dataset/test/scene_010_grass \
  --gpu-ids 0 --workers-per-gpu 1 --dry-run
```

该命令校验计划与素材，不启动 UE 或 Blender。实际烟雾测试可在相同命令中增加 `--targets Target_001 --task-limit 1`、指定独立 `--output-root` 和 `--run-dir`，核对目标确实在该地图清单内后移除 `--dry-run`。

## 全量任务和恢复

```bash
python3 scripts/render_forest_dataset.py \
  --run-dir Saved/RenderRuns/my_session \
  --output-root /absolute/dataset --gpu-ids auto --dry-run
```

确认计划后移除 `--dry-run`。新任务使用新的目录；已有任务通过同一命令加 `--resume` 恢复。可改变 GPU 数量和并发，已记录的采样与清单必须兼容。

多 worker 使用独立端口、序列目录和 GPU 映射。相机 JSON 自动导出，默认每条序列为 25 视角、512×512；其他实验协议须显式使用 `--skip-camera-json`。

验收方法、当前服务器 session 和引擎补丁见[森林数据集](../../../FOREST_DATASET.md)。
