# MatrixCityPlugin

UE5.6 合成数据插件，提供相机轨迹、OCC/GT Movie Render Queue 渲染、精确序列 FBX 导出和 C++ 渲染同步。

完整部署、构建和测试步骤见[仓库 README](../../README.md)。森林正式采集使用 `scripts/render_forest_dataset.py`；单 split 调度器为 `misc/render_dataset.py`。

- [快速开始](docs/Get-Started.md)
- [配置与执行流程](docs/DATA_GENERATION_PIPELINE.md)
- [森林数据集与恢复](../../FOREST_DATASET.md)
- [相机 JSON 协议](../../docs/CAMERA_JSON_EXPORT.md)
- [仓库维护清单](../../docs/REPOSITORY_MAINTENANCE.md)

`misc/run_cmd_async.py` 是单 UE worker 启动器；`misc/run_parallel.py` 转发到当前调度器。独立相机元数据导出使用根目录 `scripts/export_camera_json.py`。

`misc/user.json` 为本机配置，不进入版本管理。首次部署从 `misc/user.example.json` 复制，并设置本机 UE 路径。`Binaries/` 由本机 UE 从 `Source/` 编译，插件蓝图、材质和图标继续随源码版本管理。
