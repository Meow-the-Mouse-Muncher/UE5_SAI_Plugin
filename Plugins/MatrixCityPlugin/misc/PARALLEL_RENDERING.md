# 森林数据集并行渲染

使用项目根目录 `scripts/render_forest_dataset.py` 依次调度 train 和 test。命令、素材部署和配置见[仓库 README](../../../README.md)，服务器 session 恢复和验收见[森林数据集](../../../FOREST_DATASET.md)。

`--gpu-ids auto` 选择检测到的 GPU，`--workers-per-gpu auto` 结合显存与主存估算并发，`--vram-budget-gb` 默认为每卡 100 GiB。预算是分配估算，实际消耗随场景变化。可显式传入物理编号或 GPU UUID。

每个 worker 有独立端口、序列命名空间和日志。正式计划来自 `Config/Dataset/` 的批准 CSV。每条序列 25 视角，OCC/GT 保留地形与地被，仅切换清单中的原树。

UE 5.6.1 的 TraceWorker 在主线程关闭并释放 LLM 内存统计器时可能仍通过启动日志访问旧 Tracker，造成日志初始化之前的 SIGSEGV。worker 启动固定加入 `-notracethreading -traceautostart=0`，停用启动阶段的后台分析线程及 Insights 自动连接，避免该竞态。这些参数不更改渲染质量或采集配方。

恢复使用原 `--run-dir` 与输出，并加 `--resume`。GPU 数量和并发可调整，采样和清单必须保持兼容。停止时使用 Ctrl+C，等待调度器完成 worker 清理。
