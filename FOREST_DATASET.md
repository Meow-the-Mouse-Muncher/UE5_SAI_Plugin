# 终版森林数据集

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

不指定GPU编号时只自动选择空闲GPU。初次验证采用单worker，原场景的双worker显存预算不能直接套用终版实体地被场景。GPU UUID与Vulkan适配器匹配、深度读回兼容设置、独立worker端口与序列目录保留。

默认输出为 `Saved/MovieRenders/forest_dataset/train` 或 `test`，可用 `--output-root`指定独立目录。`Saved/RenderRuns/.../plan.json`、`sequence_index.csv`、`progress.json`和`result.json`记录运行状态。恢复时复用相同 `--run-dir`、输出和参数，并加 `--resume`；缺帧不能作为成功任务跳过。

## OCC、GT和输出

OCC/GT按终版清单中的树木内部Actor名称切换。OCC显示原树，GT隐藏原树；地形、草、石块及其他背景物件保留。互斥Target中仅当前完整目标可见。

每条序列保留25个相机视角，轨迹包括 `fix_line`、`plane_grid`、`rot_arc`、`rot_spiral`、`rand_shell`。默认512×512、空间采样8、时间采样1，OCC和GT均输出25帧RGB PNG及深度EXR，并导出序列FBX。此前修正的轨迹几何、参考视角和深度渲染设置保留。

运行时把材质时间固定为0秒，使OCC和GT使用相同植物风动相位。地图原材质和风动功能保留；采集不缩树、移树或重建地形。既有场景预览使用正常风动，不能与固定相位深度逐像素对照。

## 验收与限制

`Config/Dataset/asset_manifest.json`记录素材SHA，渲染前会核对实际文件；`dataset_manifest.json`记录地图与完整Target。更名及静态几何/遮挡的历史验收证据保存在user1原工程的 `Saved/ForestDataset/`。本次moqi迁移和实际渲染证据位于当前工程的 `Saved/Migration/`。

已接受的408个Target/地图状态均有树木遮挡，平均约86.7%，静态检查未发现穿模。为保留原树布局，scene_007的Target_006、scene_009的Target_005与Target_016分别整体平移约0.5、4.5、1.75米，内部组合保留。

静态零相交不证明所有风动时刻都无接触。为兼容互斥目标，部分目标附近地被较疏，落叶底材仍有纹理重复感；当前地图不宣称达到照片级自然度。

本轮仅进行小规模部署验证；正式17280/1920条采集由上述正式命令启动。

user1上的历史验证已通过：8张地图、12条完整序列、600张RGB、600张可解码且有限非零的深度EXR及12份FBX；五种轨迹均覆盖。渲染后613个素材SHA保持一致。历史结果保存在user1原工程的 `Saved/ForestDataset/render_validation.json` 和 `server_ready.json`。


moqi实际验收通过：8张地图覆盖四类环境及训练/测试集合，12条完整序列覆盖五种轨迹，共600张RGB、600张可解码且有限非零的深度EXR、12份FBX。渲染后613个素材SHA均未改变。仅完成上述小规模验收，正式17280/1920条采集由前述命令启动。

验收汇总为 `Saved/Migration/moqi_ready.json`，运行环境为 `runtime_environment.json`；两轮逐帧检查报告位于 `smoke/five_trajectories/render_validation.json` 和 `smoke/seven_environments/render_validation.json`。部署传输校验覆盖702个文件、4,124,271,840字节，未迁移原始渲染输出、旧缓存和无关素材。
