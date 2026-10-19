# 数据渲染调度与性能测试

统一入口为 `misc/render_dataset.py`。它将场景和轨迹组合划分为互不重叠的任务，动态分配给指定 GPU 上的 UE 进程。`misc/run_parallel.py` 保留为兼容入口，转入同一个调度器。

以下命令在插件根目录执行：

```bash
cd /home_ssd/sjy/UE5_Project/PCGBiomeForestPoplar/Plugins/MatrixCityPlugin
```

启动 Python 需支持 Python 3.9 及以上并安装 PyYAML；UE 使用 `misc/user.json` 中的 `ue_command` 和 `ue_project`。首次运行前应已编译项目与插件。

本版本新增原生 `MatrixCityRenderSyncLibrary`，更新插件源码后**必须重新编译并重启 UE**，仅复制 Python 文件不足以运行。相关源码位于：

```text
Source/MatrixCityPlugin/Public/MatrixCityRenderSyncLibrary.h
Source/MatrixCityPlugin/Private/MatrixCityRenderSyncLibrary.cpp
```

Python 通过 `unreal.MatrixCityRenderSyncLibrary.synchronize_rendering(True)` 调用。原生函数从游戏线程将 GPU 等待命令加入渲染队列，执行 `SubmitAndBlockUntilGPUIdle()`，再用 `FlushRenderingCommands()` 等待命令完成；不增加同步 GC。桥接类缺失、返回失败或调用抛异常时，worker 会发送 `Pipeline Failed` 并退出，需检查编译结果和 UE 日志。

## 常用命令

先查看任务计划，不启动 UE、不写入输出：

```bash
python3 misc/render_dataset.py --gpu-ids 1 --workers-per-gpu 2 \
  --maps scene_010 --shards-per-scene 4 --dry-run
```

仅使用物理 GPU 1，在单个场景上并发两个 UE 进程：

```bash
python3 misc/render_dataset.py --gpu-ids 1 --workers-per-gpu 2 \
  --maps scene_010 --shards-per-scene 4 --sync-profile balanced \
  --output-root /home_ssd/sjy/render_outputs/test_run_01 \
  --run-dir /home_ssd/sjy/render_runs/test_run_01
```

同一张 GPU 渲染多个场景：

```bash
python3 misc/render_dataset.py --gpu-ids 1 --workers-per-gpu 2 \
  --maps scene_007 scene_008 scene_010 --shards-per-scene 2 \
  --sync-profile balanced \
  --output-root /home_ssd/sjy/render_outputs/multi_run_01 \
  --run-dir /home_ssd/sjy/render_runs/multi_run_01
```

每张指定 GPU 启动一个 UE 进程：

```bash
python3 misc/render_dataset.py --gpu-ids 0,1 --workers-per-gpu 1 \
  --maps scene_007 scene_008 scene_010 --sync-profile legacy \
  --output-root /home_ssd/sjy/render_outputs/two_gpu_run_01 \
  --run-dir /home_ssd/sjy/render_runs/two_gpu_run_01
```

仅选择目标物时可加 `--targets Target_001 Target_002`，名称必须与场景 Actor 标签完全一致。省略时处理场景内全部 `Target_` 目标；隐藏其他目标的逻辑仍使用完整 Actor 列表。

## GPU 编号与进程绑定

`--gpu-ids` 接受 `nvidia-smi` 中的物理编号或 NVIDIA UUID，多个设备用逗号分隔。默认 `auto` 只选择启动采样时显存占用低于 1024 MiB、GPU 利用率低于 10% 的设备；没有满足条件的 GPU 时退出。`auto` 可能选中多张空闲卡，需要固定某张卡时应显式传入编号。

UE 在 Linux 上使用 Vulkan，单独设置 `CUDA_VISIBLE_DEVICES` 不能保证选择正确的渲染设备。启动器通过 NVIDIA UUID 匹配实际 Vulkan 适配器，再传入对应的 `-graphicsadapter`，并记录 `GPU_BINDING`。找不到唯一匹配时停止启动。不要将物理 GPU 编号直接当作 Vulkan 枚举顺序。

`--workers-per-gpu` 是每张卡最多同时运行的 UE 进程数，默认 1。可以用 2 对比吞吐量与显存占用；进程数增加后的效果需要实测。调度器不会为了腾显存结束已有任务。独立调度器之间没有 GPU 预留机制，避免同时启动多个 `auto` 调度器争用同一张卡。

## 多进程兼容设置与深度读回

通过 `render_dataset.py` 生成的 worker 配置启动 UE 时，启动器自动加入以下参数：

```text
-Multiprocess
-NoAssetRegistryCacheWrite
-ini:Engine:[ConsoleVariables]:AssetRegistry.DisableDirectoryWatcher=1
-ini:EditorPerProjectUserSettings:[/Script/UnrealEd.EditorLoadingSavingSettings]:bMonitorContentDirectories=False
-ini:Engine:[ConsoleVariables]:r.Vulkan.Submission.AllowTimelineSemaphores=0
```

前两项保留共享 AssetRegistry 缓存的读取，同时禁止并发写缓存及共享配置，避免多个 UE 进程争用 `CachedAssetRegistry_0.bin.tmp`。两项目录监视设置分别关闭 AssetRegistry 热更新监视与自动重导入的内容目录监视，减少服务器用户级 inotify watches 的消耗；没有修改系统 `sysctl`。当前任务通过 AssetTools 显式创建并保存独立序列，不依赖其他进程的文件变更通知。此启动方案不需要追加 `UserDir`。

本机一次同 GPU 双 worker 测试中，已观察到一个进程在首个渲染队列的深度通道读回阶段停止前进。线程栈定位为 `PostProcessVisualizeBuffer.cpp::ReadbackPixelData` → `VulkanRenderTarget.cpp::RHIReadSurfaceFloatData` → `VulkanSubmission.cpp::RHIBlockUntilGPUIdle`，等待 timeline 路径的完成事件；该队列尚未调用 Python 完成回调和新增的原生屏障。这与图像输出后的 Python 等待是不同位置的问题。

最后一项使用 UE5.6 的普通 fence 兼容路径，关闭 timeline semaphore。引擎在此模式下也不创建独立的 Vulkan submission/interrupt 线程，改为直接处理提交与完成队列，避开上述异步事件等待路径。它不关闭 CPU 读回，不改变分辨率、空间/时间采样、图像通道、OCC/GT 帧数或深度数值。此方案已在 GPU1 的单场景双 worker 和双场景并发测试中完整通过；本次样本没有再次出现挂起，其他驱动或场景仍需根据实际运行观察。

这些 CVar 必须在启动期设置，不能等 `ExecCmds` 中的 Python 开始运行后再改。不要使用 `r.Vulkan.IgnoreCPUReads=1` 绕过问题：该调试项会以零替代 GPU 读回数据，导致深度输出失效。最终请以生成完成标记、实际 PNG/EXR 数量及数据检查结果判断运行是否成功。

## 单场景与多场景如何划分

轨迹任务来自 `trajectory_config.yaml` 中以下三个列表的笛卡尔积：

```text
global.trajectory_types × global.camera_heights × global.plane_angles
```

`--shards-per-scene N` 将每个场景的轨迹任务交错分成最多 N 份；每份处理指定目标物上的这些完整轨迹。单条轨迹的 OCC、GT 归同一任务，不拆分其帧。

所有场景的分片进入同一个待办队列，空闲 worker 领取下一个任务，因此可同时运行同场景的不同轨迹，也可运行不同场景。一个 worker 一次只启动一个 UE 进程；每个分片完成后 UE 退出，再处理下一片。增加分片也会增加地图加载与 UE 启停次数。

未设置 `--shards-per-scene` 时，若场景数少于总 worker 数，自动使用 `总 worker 数 × 2` 份；否则每个场景 1 份。分片数不会超过实际轨迹任务数。

场景名称从项目 `Content/Map` 下递归解析，支持 `dense` 等子目录；也可显式传 `/Game/Map/.../scene_001`。同名地图存在歧义时必须指定完整路径。若不同目录中地图名称相同，不能放在同一次输出计划中。

未传 `--maps` 时优先使用 `user.json` 的 `maps`；没有配置列表时递归扫描地图。`--task-limit N` 只保留配置顺序中的前 N 个轨迹组合，适合小规模测试，会减少数据集，正式全量生成应省略。

## 配置与数据质量

| 内容 | 配置入口 |
|---|---|
| UE 程序、项目、Python 脚本、默认场景 | `misc/user.json` 或 `--config` |
| 轨迹种类、高度、角度、尺寸、帧数 | `misc/trajectory_config.yaml` 或 `--trajectory-config` |
| 分辨率、通道、抗锯齿、输出 | `user.json` 的 `render_config` 或 `--render-config` |
| 本次输出根目录 | `--output-root`，优先于渲染 YAML 的 `Output_Path` |
| 分片、GPU、worker 数、同步等待方案 | 调度器命令参数 |

当前数据配置使用五种轨迹 `fix_line`、`plane_grid`、`rot_arc`、`rot_spiral`、`rand_shell`，排除 `rot_line`。本次优化保留原有轨迹几何及渲染质量：**每条轨迹 25 帧 OCC、25 帧 GT，512×512，空间采样 8、时间采样 1，RGB PNG 与深度 EXR**。调度器不自动降低分辨率、采样数或关闭通道；实际质量仍以选用的渲染配置为准。

`Single_Frame_Only` 是既有的补帧选项，普通全量配置应保持 `false`。流水线没有默认将 GT 改为单帧。`Only_OCC` 也保持原配置语义。

默认 `--seed 42` 对每个场景、目标和轨迹组合生成稳定随机种子，随机轨迹不因 worker 数或执行顺序改变。球壳轨迹仍以完整配置中的高度范围确定半径边界，分片不会缩小范围。

## 同步等待方案

`--sync-profile legacy` 为默认方案，保留原有安全等待；`balanced` 缩短步骤间的等待并减少 GC 次数，不改变渲染样本或数据帧数。以下是渲染 YAML 未另设 `Performance_Settings` 时的参数：

| 设置 | legacy | balanced |
|---|---:|---:|
| 每次队列结束后的等待循环 | 3 × 0.5 秒 | 1 × 0.25 秒 |
| 每个渲染步骤结束后的等待 | 1 秒 | 0.1 秒 |
| 队列结束时 GC 请求 | 每个循环一次 | 每个循环一次 |
| 步骤结束时额外 GC 请求 | 两次 | 关闭 |
| 地图结束等待 | 3 × 1 秒 | 2 × 0.5 秒 |
| 退出前等待 | 2 × 1.5 秒 | 1 × 0.5 秒 |

历史代码的等待与 Vulkan 清理稳定性有关。若场景或驱动下出现清理崩溃，使用 `legacy` 对照。`balanced` 是否提高实际吞吐量以本机同等任务测试为准，不保证 GPU 利用率持续达到某个数值。

两种方案现在都使用真正的原生渲染/GPU 屏障；等待循环设为 0 时仍至少执行一次屏障。表中的时间是屏障以外的附加等待。`SystemLibrary.collect_garbage()` 只是请求后续引擎 tick 执行 GC，多次请求不等于已经完成多轮 GC。阻塞 Python 的 `sleep` 也不会替代渲染同步或让游戏线程推进到下一 tick。屏障并不保证消除所有 Vulkan 或驱动问题。

旧代码曾调用 `execute_console_command(None, 'r.FlushRenderingCommands', False)`：第三个参数实际是 `PlayerController`，传布尔值会抛异常；本机 UE5.6 源码也没有相应控制台指令注册。旧回调因而可能进入固定延时的异常分支。本版本已使用原生桥接替换，并在失败时明确中止；仅将 `False` 改为 `None` 不能实现同等同步。和旧版本比较性能时，应记录这一差别。

自定义性能参数可放进渲染 YAML 的 `Performance_Settings`；调度器选择的方案覆盖其中的同名项。`legacy` 本身不增加覆盖项。

## 输出、隔离与日志

输出保持原有数据命名规则：

```text
<output-root>/
  <trajectory_type>/
    scene_010_Target_001_height_050_ang_000_occ/{rgb,depth}/...
    scene_010_Target_001_height_050_ang_000_GT/{rgb,depth}/...
  Exported_FBX/<trajectory_type>/...fbx
```

每个 worker 只创建本分片的序列资产，路径为：

```text
/Game/Sequences/_workers/<run_token>/<scene_name>_sNNN/<trajectory_type>/...
```

地图与场景相机不保存到磁盘；可见性修改仅存在各自 UE 进程内。图像与 FBX 文件名包含场景、目标、高度和角度，轨迹类别位于上级目录。同一次调度中的分片互不覆盖。高度与角度若取整后造成文件名重名，启动前会报错。

独立的生成任务应使用不同的 `--output-root` 和 `--run-dir`。只改 `--run-dir` 不会改变图像文件名；两个独立任务向同一输出根目录生成相同数据，会相互覆盖。当前未提供跨调度器的文件锁，同一运行目录也只能由一个调度器操作。

运行记录默认保存在项目 `Saved/RenderRuns/<时间与随机标识>/`，也可用 `--run-dir` 指定。主要文件为：

| 文件 | 用途 |
|---|---|
| `plan.json` | GPU、输出位置、全部分片和配置指纹 |
| `launch.json`、`trajectory.yaml`、`render.yaml` | 本次启动配置快照；渲染继承配置已展开 |
| `<job_id>/worker.json` | 精确任务、独立序列路径、遥测路径等 |
| `<job_id>/ue.log`、`launcher.log` | UE 与启动器日志 |
| `<job_id>/steps.jsonl` | 每步耗时、期望输出帧数和实际文件统计 |
| `<job_id>/done.json` 或 `failed.json` | 分片结果 |
| `progress.json`、`result.json` | 本次执行进度和汇总 |

只有完整流水线的 `Pipeline Finished` 才表示成功；单个 OCC/GT 队列结束不代表整个分片完成。启动器收到完成标记后继续等待 UE 正常退出，保留最终清理时间。生成、渲染、FBX 导出失败会产生失败标记及非零退出码。

## 中断与恢复

`Ctrl+C` 可中断当前调度；`--timeout 秒数` 限制每个 UE worker 的运行时间，默认 0 表示不设时限。不要使用全局 `pkill UnrealEditor` 结束任务。

恢复时复用原来的输出目录、运行目录及配置，增加 `--resume`：

```bash
python3 misc/render_dataset.py --gpu-ids 1 --workers-per-gpu 2 \
  --maps scene_010 --shards-per-scene 4 --sync-profile balanced \
  --output-root /home_ssd/sjy/render_outputs/test_run_01 \
  --run-dir /home_ssd/sjy/render_runs/test_run_01 --resume
```

恢复检查任务、渲染配置、轨迹配置、种子及同步方案的指纹。对于有 `done.json` 的分片，还比较其图像统计；一致时跳过，其余分片整体重跑。这里的统计包括文件数量、字节数和目录名称，不是内容哈希校验。恢复不是逐帧续写，未完成分片可能覆盖已有的部分输出。先确认原调度器已结束，再启动恢复。

默认遇到失败后不再领取新任务，已经启动的其他 worker 会结束当前分片。若希望记录失败后继续处理剩余分片，可加 `--keep-going`；最终存在失败仍返回非零退出码。

## 同等任务性能对比

`misc/benchmark_render.py` 可包装生成命令，采样目标 GPU 的利用率、显存、功率、温度及输出数量，并生成 JSON、CSV 和日志。应使用相同场景、目标、轨迹、种子、渲染配置和固定分片数，对比 `legacy/单 worker`、`balanced/单 worker`、`balanced/双 worker`；各次使用独立输出与运行目录。

例如，先测试一个目标、两个轨迹组合：

```bash
python3 misc/benchmark_render.py --gpu-id 1 \
  --output-dir /home_ssd/sjy/render_outputs/bench_legacy_01 \
  --result-dir /home_ssd/sjy/render_benchmarks --label legacy_01 \
  --timeout 1800 -- \
  python3 misc/render_dataset.py --gpu-ids 1 --workers-per-gpu 1 \
    --maps scene_010 --targets Target_001 --task-limit 2 \
    --shards-per-scene 2 --sync-profile legacy \
    --output-root /home_ssd/sjy/render_outputs/bench_legacy_01 \
    --run-dir /home_ssd/sjy/render_runs/bench_legacy_01
```

对比时更换 `--label`、输出与运行目录，分别调整方案和 worker 数；其他参数保持相同。测试结束主要比较 RGB 帧数/总耗时、图像文件数/总耗时、显存峰值及 GPU 活跃窗口利用率。总耗时包括 UE 启动、地图加载、编码、写盘与退出；首次着色器或缓存构建会影响结果，需要区分冷启动与缓存已就绪的运行。

基准程序默认要求所选 GPU 空闲，并检查本次启动的进程实际使用的 GPU；发现实质工作落到其他 GPU 时会终止本次测试进程。图像文件统计只用于吞吐量测量，不代替图像内容或数据协议验证。

## 2026-10-17 本机实测

物理 GPU1 为 RTX 3090 24 GiB。scene_010、Target_001、前 4 个 fix_line 高度/角度组合，均生成 200 张 RGB PNG、200 张深度 EXR、4 个 FBX。每条轨迹 OCC/GT 各 25 帧，512×512、空间采样 8、时间采样 1。

| 方案 | 总耗时 | 图片/秒 | 整程 GPU 均值 | 输出窗口 GPU 均值 | 显存峰值 |
|---|---:|---:|---:|---:|---:|
| 原同步逻辑，单进程，热缓存 | 173.34 秒 | 2.308 | 15.58% | 19.64% | 10.44 GiB |
| 原生同步＋balanced，单进程（中间版本） | 148.87 秒 | 2.687 | 16.69% | 20.30% | 10.43 GiB |
| 最终兼容路径＋balanced，双进程 | 105.04 秒 | 3.808 | 28.99% | 43.40% | 20.62 GiB |

最终方案相对热缓存基线，吞吐提升 65.0%，耗时减少 39.4%。总耗时包括 UE 启动、加载、渲染、写盘、FBX 导出及退出。输出窗口按文件出现时间采样估计；GPU 利用率是活动时间采样，不是理论算力占比。首轮基线 189.42 秒，不用于上述加速比。

双场景 scene_010/scene_011 各一个组合、各一个目标的并发检查也完整通过：200 张图、2 个 FBX、69.58 秒，两个 worker 均正常退出。全部数据写在项目 `Saved/PerfBench` 的独立目录，没有启动正式全量生成。测试仅覆盖上述小样本，不应将耗时直接外推到所有场景和轨迹。

原始采样、命令、步骤记录及输出检查位于 `Saved/PerfBench/<case>/`；主要成功 case 为 `baseline_warm`、`balanced_1`、`balanced_2c`、`multi_scene_smoke`。早期被监控误停或挂起的诊断轮次不计入性能结果。41 项回归检查通过。
