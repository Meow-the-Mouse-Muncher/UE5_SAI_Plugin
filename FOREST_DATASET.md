# 终版森林数据集

首次部署与本机配置模板见 [README](README.md)。`misc/user.json` 现为本地配置，保留当前服务器已有文件；新部署从 `misc/user.example.json` 复制后设置本机路径。下文服务器 session、缓存与数据路径是部署记录，迁移时需另行保留。

论文采集使用的28张终版地图位于 `/Game/Map/dataset/`，新增素材位于 `/Game/ForestDataset/`。旧版地图、配置和构建脚本已移出活动工程；本地恢复材料保存在工程外的 `D:/sjy/小论文-中国图像图形学报/_work/final_maps_20261020/history`。

更名后已逐图核对所有Actor、网格组件和地被实例的变换与材质引用，并核对36份自有网格的实际渲染顶点和三角索引。目标组合、树木布局和地形保持已验收的状态。

## 地图与数据划分

| 源场景 | 集合 | 完整地图 |
|---|---|---|
| scene_007 | train | scene_007_grass、scene_007_road、scene_007_litter、scene_007_edge |
| scene_008 | train | scene_008_grass、scene_008_road、scene_008_litter、scene_008_edge |
| scene_009 | train | scene_009_grass、scene_009_road、scene_009_litter、scene_009_edge |
| scene_010 | test | scene_010_grass、scene_010_road、scene_010_litter、scene_010_edge |
| scene_011 | train | scene_011_grass、scene_011_road、scene_011_litter、scene_011_edge |
| scene_012 | train | scene_012_grass、scene_012_road、scene_012_litter、scene_012_edge |
| scene_013 | train | scene_013_grass、scene_013_road、scene_013_litter、scene_013_edge |

完整包路径例如 `/Game/Map/dataset/train/scene_007_grass`、`/Game/Map/dataset/test/scene_010_road`。每张地图包含对应源场景的全部互斥Target；一个Target可包含多个人或多辆车，采集仍按完整Actor处理。

训练24图，共17280条完整序列，每图720条；测试4图，共1920条，每图480条。训练/测试序列约9:1，不设验证集。同一原始场景的全部环境类型归属同一集合。正式清单位于 `Config/Dataset/`。

## 在moqi_h20_UE渲染

2026-10-21更新：本轮采集使用修复后的引擎。正式渲染已按用户要求停止并保留数据；后续使用下节“断点续跑、多卡与相机JSON”的持久入口。本节旧引擎命令和后文一次性清空启动器保留作历史记录。

项目部署在 `/mnt/nas_b/sjy/code/PCGBiomeForestPoplar/`，直接使用现有 `Plugins/MatrixCityPlugin/misc/render_dataset.py` 入口和 `misc/user.json`。UE使用 `/opt/UE_5.6` 的5.6.1，通过已有 `/usr/local/bin/ue56` 启动。

```bash
cd /mnt/nas_b/sjy/code/PCGBiomeForestPoplar

# 检查正式计划与素材，不启动UE。
python3 Plugins/MatrixCityPlugin/misc/render_dataset.py --split train --gpu-ids 0 --dry-run

# 正式训练集。
python3 Plugins/MatrixCityPlugin/misc/render_dataset.py \
  --split train --gpu-ids 0 --workers-per-gpu 1 --sync-profile balanced

# 正式测试集。
python3 Plugins/MatrixCityPlugin/misc/render_dataset.py \
  --split test --gpu-ids 0 --workers-per-gpu 1 --sync-profile balanced
```

root运行时，原入口根据 `user.json` 自动切换为非root的 `ue` 用户，并为GPU预探测和UE设置相同的NVIDIA Vulkan环境。H20为物理GPU0；项目及输出目录须允许 `ue` 写入。

系统Python负责调度和验证，UE内置Python3.11负责场景脚本；两者使用各自现有的site-packages，不创建虚拟环境。UE依赖版本记录在 `scripts/forest_dataset/requirements-ue-linux.txt`。

当前H20实际使用VULKAN_SM6。驱动未提供UE5.6硬件光追配置所需的 `rayTracingPositionFetch` 特性，UE自动禁用硬件光追；项目已启用网格距离场，Lumen可以使用软件回退。分辨率、采样和Nanite配置保留。正式采集统一在moqi完成，以保持渲染后端一致。

默认按CSV中已批准的Target/高度/方向/轨迹组合渲染，保留既定预算。`--maps`可接完整包路径，例如 `--maps /Game/Map/dataset/test/scene_010_grass`；`--targets Target_001`可只验证一个完整目标，`--task-limit 1`可限制每个Target的序列数。`--split all`选择两个集合。

默认 `--gpu-ids auto` 使用nvidia-smi检测到的全部GPU；`--gpu-ids idle` 只选择空闲卡，也可显式指定物理编号或UUID。GPU UUID与Vulkan适配器匹配、深度读回兼容设置、独立worker端口与序列目录保留。

默认输出根目录为 CPFS 上的 `/mnt/cpfs_b5/perception_data/sjy/forest_dataset`，训练集写入其 `train/` 子目录，测试集写入 `test/`，`--split all` 写入 `all/`。可用 `--output-root`直接指定输出目录。`Saved/RenderRuns/.../plan.json`、`sequence_index.csv`、`progress.json`和`result.json`记录运行状态。恢复时复用相同 `--run-dir`、输出和参数，并加 `--resume`；缺帧不能作为成功任务跳过。

## 断点续跑、多卡与相机JSON

统一入口为 `scripts/render_forest_dataset.py`，依次完成train和test并写入各自目录。它调用原生 `misc/render_dataset.py`，使用修复引擎，默认自动识别全部GPU并分配worker。当前已停止的正式session可以直接恢复，不需要删除或重新生成已有图像：

```bash
python3 scripts/render_forest_dataset.py \
  --run-dir Saved/RenderRuns/root_cause_20261020_235808/production_fresh_preparation/execution \
  --resume
```

同一命令加 `--dry-run` 只验证原计划、素材和GPU分配，不启动UE或Blender。新的全量任务使用新的 `--run-dir`，省略 `--resume`；已有输出须通过其原session恢复。

便捷Bash入口默认续跑当前session、使用CPFS输出并按每卡70 GiB预算分配进程：

```bash
bash scripts/render_forest_dataset.sh --dry-run  # 只校验计划
bash scripts/render_forest_dataset.sh            # 开始/继续渲染
```

脚本自动定位工程目录，可从任意工作目录通过绝对路径运行。`FOREST_RUN_DIR`、`FOREST_OUTPUT_ROOT`、`FOREST_CONFIG` 可覆盖运行状态、数据和机器配置路径；迁移H100时须保留原session及其train/test子目录，并提供本机有效的引擎配置。已有数据但缺少原checkpoint时会拒绝续跑。

数据位于 `/mnt/cpfs_b5/perception_data/sjy/forest_dataset/{train,test}/`。当前日志和状态位于 `Saved/RenderRuns/root_cause_20261020_235808/production_fresh_preparation/execution/`：`train_launcher.log`、`test_launcher.log` 为各split总日志，`{train,test}/<job-id>/ue.log` 为UE日志，同目录还包含 `launcher.log` 和 `camera_export.log`。`session.json`、各split的 `progress.json` 记录进度；test尚未运行时不会有相应日志。

默认 `--workers-per-gpu auto --vram-budget-gb 70` 按每个UE约9216 MiB估算；每卡总预算取70 GiB和物理容量90%的较小值，扣除已有显存占用，再按可用主存限制总并发。空闲大显存卡通常分配7个worker；同样的70 GiB预算适用于当前H20与后续H100，不因卡容量增加而自动拉高总预算。不同容量的卡可有不同worker数，共享任务队列让先空闲的worker继续领取任务。该预算是启动前估算，实际显存会随场景浮动，无法由这个参数强制锁定；可通过 `--worker-vram-mb`、`--vram-budget-gb`、`--vram-target` 调整。显式worker数超过估算预算时会拒绝启动。当前只有一张物理卡，多卡分配和GPU数量变化通过CPU调度测试验证。

断点恢复复用原 `plan.json`、`render.yaml`、`trajectory.yaml`，允许更改GPU数量及并发，不允许改变seed、采样、地图或采集清单。完整图像和非空FBX齐全的序列保留；不完整的序列重新渲染，完整任务跳过。UE在每个worker批次结束后导出FBX，因此中断批次内只有图像、尚无FBX的序列也会重渲。任务级 `render_done.json` 标记渲染阶段，`done.json` 在相机JSON也完成验证后才写入。恢复统计包含此前成功的任务，失败和完成数分别记录。session、split输出目录均有进程锁，防止并发写入同一数据。

每个任务完成图像与FBX后，自动调用 `scripts/export_camera_json.py`，通过精确序列清单导出，最多同时启动两个CPU Blender进程。OCC和GT各写入 `pose/transforms.json`，保持原25视角/512分辨率协议、OpenCV c2w和米单位平移。已有数据缺JSON时只补导出，不启动UE；JSON损坏、两份不一致或FBX哈希变化时重新导出。可用 `--blender /path/to/blender` 指定运行时。单split入口的 `--skip-camera-json` 仅用于不同分辨率、单帧或非OCC/GT的基准测试。

每个任务保存 `camera_jobs.json`、`camera_export.log` 和 `camera_export_report.json`。导出失败会使任务失败，但保留已完成的图像与FBX供下次恢复。停止统一入口时使用SIGINT/Ctrl+C，停止请求会传给当前原生调度器，并等待其协作清理。

截至停止前，72个完整任务共108000个图像文件、46,050,450,430字节；全量1,920,000个图像文件按相同均值约818.7 GB（762.5 GiB）。1080份FBX共27,682,560字节，推算19200份约0.49 GB；38400份相机JSON按实际导出样本约0.79 GB，整体约820 GB，另有少量索引/日志。当前样本主要来自scene_007，其他场景压缩率会变化，建议CPFS预留至少1 TB。

## Vulkan修复与重新采集

本轮故障调查证据保存在 `Saved/RenderRuns/root_cause_20261020_235808/`。原 `/opt/UE_5.6` 保留；修复版为独立目录 `/opt/UE_5.6_render_fix`，正式启动包装器为 `/opt/ue56-install/run-editor-fixed`。UE仍为5.6.1、模块BuildId仍为43139311；分辨率、空间采样、轨迹和地图素材不因本次引擎修复而降级。

七项修复合并在 [ue5.6.1-vulkan-correctness.patch](scripts/engine_patches/ue5.6.1-vulkan-correctness.patch)，源码和产物哈希、构建步骤、验收边界见 [对应构建记录](scripts/engine_patches/ue5.6.1-vulkan-correctness.build.json)。补丁涉及10份源码、5个模块：

| 模块 | 已修复的问题 |
|---|---|
| VulkanRHI | 已注册且不走LRU的PSO销毁改为延迟回收，避免GPU仍使用pipeline时直接销毁；虚拟纹理复制尊重RDG已提供的布局转换；空、空资源或仅Discard的转换不再分配无人回收的split-barrier event。 |
| MovieRenderPipelineCore | CancelAllJobs改为请求异步关闭，避免Slate重入期间同步清理shot数组。 |
| Renderer | 清理提取的场景纹理时一并释放PartialDepth引用，避免SceneDepthZ残留到设备销毁。 |
| RenderCore | 在限定的Vulkan SM6深度/模板附件路径中，让RDG访问记录与实际使用的深度/模板绑定保持一致。 |
| DerivedDataCache | 按请求RawHash读取对应版本的页面流，将RawHash纳入批量读取缓存身份；上层缓存返回不同版本时继续向下查询，防止Nanite用本版页面偏移读取另一版数据。 |

截至本段更新，五个模块均已编译并通过导出符号与动态链接检查。只读复核确认原版34项、修复源码10项和模块产物15项哈希全部匹配；补丁在原版上的正向检查和修复版上的反向检查通过，内存应用补丁后的10份完整源码与修复版逐字节一致。新增DerivedDataCache模块保留全部6540个原导出符号，已有四个修复模块未被改写。构建和链接检查不能替代运行验收。

此前六项修复的1并发已完成两波，共2个job、4条序列、200张PNG、200张EXR和4份FBX；4并发完成两波及worker替换，共8个job、16条序列、800张PNG、800张EXR和16份FBX；8并发完成一波，共8个job、9条序列、450张PNG、450张EXR和9份FBX。三档逐帧解码均通过，全部UE及调度器返回0，Vulkan验证层未记录错误，退出后的独立GPU探针通过且没有新增Xid。证据在 `capacity_preparation/tier_01/`、`tier_04/`、`tier_08/` 的输出验收、原生结果和健康报告中，路径均相对于上述调查目录。

原12并发测试在冷构建共享StaticMesh缓存时出现另一类故障：scene_009和scene_012的edge/grass四个worker先报告坏Nanite fixup头，再触发CPU断言并返回139；其他worker被取消，调度器返回130。这次没有VUID或新增Xid，退出后的GPU探针通过。日志同时记录同StaticMesh键的非确定性构建结果；源码显示旧文件缓存忽略请求RawHash，可把某版页面偏移配到另一版页面流。随后受控冷构建复测已捕获三种StaticMesh键的请求RawHash与缓存记录RawHash不同，并进入按内容哈希读取的回退路径，证明版本错配确实会发生，原日志行保存在 `nanite_ddc_fix_proposal/runtime_generation_mismatch_evidence.json`。旧故障现场未捕获对应哈希，仍不据此断言每个旧失败页或所有历史Xid的唯一原因。同一scene_009_grass在共享缓存已预热后单worker完成2条序列、100张PNG、100张EXR和2份FBX，正常退出且输出验收通过，证据见 `nanite_single_009/`。

最终七项修复版本的12并发冷构建复测 `capacity_preparation/tier_12_ddc_fixed/` 已完整通过：12个job、24条序列、48个MRQ步骤，1200张PNG、1200张EXR和24份FBX全部验收通过；RGB与深度逐帧解码完成，深度均有限且非全零，12个UE及调度器全部返回0，VUID为0，退出后GPU探针通过且没有新增Xid。复测使用独立缓存副本，只移除三条StaticMesh记录引用以触发并发重建，保留按内容哈希存储的数据及原缓存；Linux环境使用 `UE_LocalDataCachePath`，DDC日志实际捕获版本错配并进入正确版本的读取路径。22项CPU分支契约也已通过。最终输出、原生退出及健康证据在该目录的 `render_validation.json`、`run/result.json`、`finished.json`、`validation_errors.json` 和 `post_stop_health.json`。

正式渲染并发选择12。容量采样峰值显存124677/143771 MiB（86.72%）；按“12个worker均存活、GPU利用率≥50%、显存≥84000 MiB”筛选的62个样本，显存中位数105476 MiB（73.36%），GPU利用率中位数97%。采样不表示启动及退出阶段始终达到70%，验证层下的耗时也不作为正式产能预测；统计见 `capacity_validation_summary.json`。1/4/8并发通过属于此前六项修复版本，12并发通过属于最终七项修复版本。原12并发失败证据保留；有限测试不能证明所有历史Xid的唯一原因，也不能保证长期运行永不故障。

用户已要求删除当前正式渲染数据后，从完整训练/测试清单重新采集。独立准备目录为 `Saved/RenderRuns/root_cause_20261020_235808/production_fresh_preparation/`，旧 `production_resume_preparation/` 仅保留作审计。新启动器默认只生成计划，`--workers` 必填，正式执行还需显式加 `--execute`：

```bash
# 容量验收选择12并发；以下命令只校验全量计划，不启动UE。
python3 Saved/RenderRuns/root_cause_20261020_235808/production_fresh_preparation/launch_production.py --workers 12

# 另行清空正式train/test后，使用正式执行参数。
python3 Saved/RenderRuns/root_cause_20261020_235808/production_fresh_preparation/launch_production.py --workers 12 --execute
```

新计划使用 `Config/Dataset/train_sequences.csv` 的17280条和 `test_sequences.csv` 的1920条，依次训练后测试，每个地图/目标拆3个shard，共1152和72个job。参数为512×512、空间采样8、时间采样1、seed42、OCC+GT、PNG+EXR和FBX；全量预计960000张PNG、960000张EXR和19200份FBX。正式运行不带Vulkan验证层或调试器。

启动器不包含删除操作。执行前必须由外部明确完成正式 `train/`、`test/` 的清理并保留记录，`_diagnostics/` 不在清理范围；启动器会拒绝非空或经符号链接重定向的正式目录，并检查已有正式worker。锁和一次性执行目录防止重复启动，任一split非零退出立即停止，不自动重试；取消只向native scheduler发信号并至少留90秒清理，不向整组进程强制发信号。

正式全量采集已按12并发启动，旧 `train/`、`test/` 已清空并重新创建，删除审计见调查目录的 `formal_dataset_deletion.json`。新任务的持久状态为 `production_fresh_preparation/execution/session.json`，训练调度日志为 `production_fresh_preparation/execution/train_launcher.log`；训练完成后自动进入测试集。实际输出根目录为 `/mnt/cpfs_b5/perception_data/sjy/forest_dataset/`。默认 `misc/user.json` 已切换修复引擎；Linux缓存变量改为 `UE_LocalDataCachePath=/opt/ue56-cache/ddc-render-fixed`，原缓存仍保留。

## OCC、GT和输出

OCC/GT按终版清单中的树木内部Actor名称切换。OCC显示原树，GT隐藏原树；地形、草、石块及其他背景物件保留。互斥Target中仅当前完整目标可见。

每条序列保留25个相机视角，轨迹包括 `fix_line`、`plane_grid`、`rot_arc`、`rot_spiral`、`rand_shell`。默认512×512、空间采样8、时间采样1，OCC和GT均输出25帧RGB PNG及深度EXR，并导出序列FBX。此前修正的轨迹几何、参考视角和深度渲染设置保留。

运行时把材质时间固定为0秒，使OCC和GT使用相同植物风动相位。地图原材质和风动功能保留；采集不缩树、移树或重建地形。既有场景预览使用正常风动，不能与固定相位深度逐像素对照。

## 验收与限制

`Config/Dataset/asset_manifest.json`记录素材SHA，渲染前会核对实际文件；`dataset_manifest.json`记录地图与完整Target。更名及静态几何/遮挡的历史验收证据保存在user1原工程的 `Saved/ForestDataset/`。本次moqi迁移和实际渲染证据位于当前工程的 `Saved/Migration/`。

已接受的408个Target/地图状态均有树木遮挡，平均约86.7%，静态检查未发现穿模。为保留原树布局，scene_007的Target_006、scene_009的Target_005与Target_016分别整体平移约0.5、4.5、1.75米，内部组合保留。

静态零相交不证明所有风动时刻都无接触。为兼容互斥目标，部分目标附近地被较疏，落叶底材仍有纹理重复感；当前地图不宣称达到照片级自然度。

此前小规模部署验证记录保留如下；当前17280/1920条正式全量采集的运行状态见上节。

user1上的历史验证已通过：8张地图、12条完整序列、600张RGB、600张可解码且有限非零的深度EXR及12份FBX；五种轨迹均覆盖。渲染后613个素材SHA保持一致。历史结果保存在user1原工程的 `Saved/ForestDataset/render_validation.json` 和 `server_ready.json`。


moqi实际验收通过：8张地图覆盖四类环境及训练/测试集合，12条完整序列覆盖五种轨迹，共600张RGB、600张可解码且有限非零的深度EXR、12份FBX。渲染后613个素材SHA均未改变。这是此前的小规模验收结果；当前正式采集状态见上节。

验收汇总为 `Saved/Migration/moqi_ready.json`，运行环境为 `runtime_environment.json`；两轮逐帧检查报告位于 `smoke/five_trajectories/render_validation.json` 和 `smoke/seven_environments/render_validation.json`。部署传输校验覆盖702个文件、4,124,271,840字节，未迁移原始渲染输出、旧缓存和无关素材。
