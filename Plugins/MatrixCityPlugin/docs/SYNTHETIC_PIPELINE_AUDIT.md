# UE5 合成数据管线审计报告

## 审查范围与结论

本报告审查 MatrixCityPlugin 当前批处理链路：外部启动器、轨迹生成、
OCC/GT Movie Render Queue（MRQ）渲染、FBX 导出、Blender 位姿提取，以及
仓库中的重聚焦/H5 脚本。

这里必须区分两件事：

* **不同轨迹产生不同样本是预期行为。** 随机轨迹用于扩大视角分布，不是
  可复现性缺陷。
* **同一条已生成轨迹的 OCC 与 GT 必须是可配对样本。** 两者必须拥有相同的
  相机位姿、帧身份、内参和输出几何；唯一预期差异是 GT 隐藏指定的 `SM_`
  前景遮挡物。

当前实现有一个正确的基础：同一轨迹的 OCC 与 GT 复用同一个
`LevelSequence` 资产路径，且上述 GT 语义符合本数据集需求。当前代码已收紧
轨迹 pose、帧集合与本轮 FBX 的关联；仍应在量产前完成坐标系标定，并保留 run
manifest 以支持完整溯源。

## 当前实现的实际流程

1. `misc/run_parallel.py` 或 `misc/run_cmd_async.py` 启动 Unreal，并执行
   `Content/Python/pipeline.py`，可附带 `-target_map`。
2. `pipeline.py` 加载地图，选择标签以 `Target_` 开头的目标，以及标签以
   `SM_` 开头的 `StaticMeshActor` 作为受控遮挡物。
3. 对每个目标，`utils_sequencer.main` 按
   `trajectory_type x camera_height x plane_angle` 展开
   `misc/trajectory_config.yaml`，并将 Level Sequence 保存到
   `/Game/Sequences/<trajectory_type>/`。
4. `CustomMoviePipeline.render_multi_trajectory_multi_height_serial` 为每条
   Sequence 创建两个 MRQ 步骤：
   * OCC：当前目标可见，其他 `Target_` 隐藏，`SM_` 可见。
   * GT：当前目标可见，其他 `Target_` 隐藏，`SM_` 隐藏。
5. 输出目录编码了轨迹类型、地图、目标、高度、角度以及 `_occ` 或 `_GT`。
   地图结束时，插件按地图名在 `/Game/Sequences` 中搜索并将匹配资产导出到
   `Exported_FBX/`。

第 4 步复用同一个 Sequence 是正确设计。本数据集的 GT 定义即为：在同一相机
动画下，仅隐藏指定 `SM_` 前景遮挡物，而非构造通用的“无任何遮挡”图像。
下面的问题集中在如何证明每个输出文件仍能正确追溯到这条相机动画。

## 设计选择，不是缺陷

### 随机轨迹是有意为之

`rand_shell` 会随机采样控制点。对于需要视角多样性的合成数据生成，这是合理
设计，不应为了“可复现”而强制所有数据使用固定种子。

每个随机样本仍应保留溯源信息。当前生成的 Level Sequence 资产和其导出的 FBX
记录了实际采样出的轨迹。后续应在 manifest 中记录其精确资产路径和哈希；随机
种子仅在需要重跑或调试时有价值，并非随机数据有效性的前提。

### GT 是隐藏指定 `SM_` 的受控对照

GT 不要求移除所有场景物体，也不要求成为目标隔离图。对于同一轨迹，OCC 保留
指定 `SM_` 前景遮挡物，GT 隐藏这些 `SM_`；这正是用于前景遮挡效果对比的语义，
不是缺陷或待验证项。数据集和下游实验应沿用这一名称与定义，避免把 GT 误读为
全场景无阻挡真值。

## 已证实的问题

### 已修复 - `rot_arc` pose 导出不再被硬编码类别跳过

`scripts/blender_extract_transforms.py:batch_process_by_category` 现在从
`Exported_FBX/` 的实际子目录发现带 FBX 的轨迹类别，因此 `rot_arc` 与其他已导出
类别均会进入 pose 转换。H5 默认白名单也覆盖 `fix_line`、`rot_arc`、
`plane_grid`、`rot_spiral` 与 `rand_shell`。

### P1 - 位姿帧身份已做集合校验，但仍缺少 UE 帧级 manifest

**当前实现。** Blender 导出器现在要求 GT/OCC 各自 RGB 与 depth 的 stem 集合
完全相同，并要求 GT 与 OCC 的集合相同、每张 RGB 分辨率一致、渲染帧数等于 FBX
提取的 pose 数。它使用真实 RGB stem 写入 `file_path`；任一条件不满足即拒绝写出
`transforms.json`。

**剩余风险。** 仍按排序后的 stem 与 FBX pose 顺序对应，尚未保存 UE tick、MRQ
文件名与相机矩阵的一一 manifest。预热帧、重复相机位姿或自定义播放范围的精确
映射仍应通过该 manifest 固化。

**整改。** 每个 sequence 先生成 manifest。每一帧必须记录 UE sequence
frame/tick、MRQ 文件 stem、图像尺寸、相机矩阵、内参和各通道路径。只有 OCC 和
GT 都包含完全相同的预期 stem 后，才能写 pose 文件。

### 已修复 - FBX 仅导出本轮明确生成的 Sequence

`CustomMoviePipeline` 现在累计 `utils_sequencer.main` 本轮返回的精确 Sequence
路径，并将该列表传给 `export_sequences_to_fbx.export_sequences_for_map`。批量渲染
完成后不再按地图名扫描历史资产；独立运行导出器时才保留旧扫描路径作为兼容回退。
输出目录仍没有 run ID 或配置哈希，量产时应补充 manifest 和发布目录隔离。

### P1 - 现行 H5 导出已清洗深度离群点；遗留常量平面脚本必须停用

**现行链路。** `Active_cam_recon/utils/process2LFdata.py` 调用
`utils/warp_utils.py:load_render_data` 导出 `lfnr-h5-v2`。它使用 GT 中心深度的
有效正值像素计算 p1/p99，先将无效像素替换为 p1，再将逐像素深度截断到该范围，
从而消除极端离群值，但不将深度替换为常量平面。它还会拒绝以下不配对输入：

* 缺失 GT 或 OCC pose 元数据；
* GT/OCC `center_idx` 或内参 `K` 不一致；
* OCC RGB 帧数与 pose 帧数不一致；
* GT RGB/depth 帧数、中心图像/深度尺寸或数值有效性不符合要求。

输出写入同目录临时 H5 后原子替换，并写入 schema 版本、源 GT/OCC 目录、内参、
中心帧和深度缩放，不能再将“当前 H5 路径会把真实深度全部替换为常量平面”列为
问题。

**使用约束。** `process2LFdata.py` 仍依赖上游已经生成的 `transforms.json`，因此
`rot_arc` pose 缺失依然会阻断 H5 转换。其 `__main__` 的默认
`ALLOWED_TRAJECTORIES` 覆盖五类训练轨迹：`fix_line`、`rot_arc`、`plane_grid`、
`rot_spiral` 与 `rand_shell`。若某次渲染启用其他类别，必须显式扩展该列表或设为
`None`。

**遗留路径。** `scripts/align_pose_to_center_inv_h5.py` 会将 GT 深度均值填满整张
深度图。该脚本不属于当前 H5 导出链路，不能用于生成新训练数据；其输出也不能与
`process2LFdata.py` 的 H5 混用。

### P1 - 局部轨迹失败仍可能发布表面完成的地图

**证据。** `utils_sequencer.main` 对失败的轨迹/高度/角度组合记录异常后继续；只要
还剩至少一个 Sequence，渲染就继续。地图结束时 `Pipeline Finished` 不会检查完整
笛卡尔积的输出。

**影响。** 某些目标可缺少高度、角度、GT 或 OCC 文件，但地图仍可能看似完成，导致
轨迹覆盖不均与比较偏差。

**整改。** 生前构造预期 key 集合。除非显式 partial-run 模式写出机器可读失败
列表，否则每个 key 必须存在一对成功的 OCC/GT 输出。

### 已修复 - 启动器配置和 worker socket 端口已传入 UE

`pipeline.py` 现在仅在未提供 `-render_config_path` 时回退到
`misc/render_SAI_config.yaml`，不会覆盖启动器传入的配置；`run_cmd_async.py` 传入
`-socket_port`，UE 端使用该端口连接。因此并行 worker 不再都试图连接 `9999`。
最终生效配置仍应写入 run manifest，以便离线溯源。

## 位姿与坐标系：需用标定结果确认的风险

下列问题很严重，但仅凭源码无法断言某一份 FBX 一定错误；必须用标定场景测量。

### P1 - 变换矩阵的缩放未分解和验证

`blender_extract_transforms.py` 在写入 `transform_matrix` 前将
`camera.matrix_world` 左上 3x3 乘以 100。另一路径同时缩放平移，而 direct 路径
不缩放平移。若 FBX 导入矩阵包含统一 UE-to-Blender 缩放，该操作可能试图消除它；
若矩阵已经刚体化，则会破坏旋转正交性。

**所需测试。** 将导入相机矩阵分解为平移、旋转和缩放；验证
`R.T @ R ~= I`、`det(R) ~= +1`，并验证已知一米相机平移。只换算平移/世界单位，
不能对未经验证的旋转块直接缩放。

### P1 - 内参由可变配置重建

Blender 脚本从 FBX 读取 FOV，却从当前 `render_SAI_config.yaml` 读取分辨率。渲染后
若修改 YAML，生成的 `fl_x`、`fl_y`、`w`、`h` 会改变，但图像不会改变。

**整改。** 每个 run 保存生效的分辨率、FOV、宽高比策略和输出配置；生成 pose 时
只读取该不可变记录。

### P1 - 下游重聚焦脚本的几何契约不一致

`align_pose_to_center_forward.py`、`align_pose_to_center_inv.py` 与
`align_pose_to_center_3d.py` 使用不同轴向/深度约定。inverse 路径接收
`src_depth_dir`，却不使用它进行重投影。没有统一标定和坐标/深度约定时，不能混用
这些脚本的结果。

## 量产前必须通过的验收实验

建立静态标定地图：一个已知尺寸的 `Target_` 目标，以及会在部分视角遮挡目标的
指定 `SM_` 面板。仅为此测试使用短固定轨迹。

对每个生成 key：

```text
(run_id, map, target, trajectory, height, angle, UE_frame)
```

必须验证：

1. OCC 和 GT 具有相同 RGB/depth 文件 stem、图像尺寸，以及该 key 对应的相机/内参。
2. OCC 和 GT 使用完全相同的 Sequence 资产路径和导出 FBX 哈希。
3. 以“GT 隐藏指定 `SM_`”为既定语义检查图像：受该面板遮挡的位置，GT 应显现
   目标；两图均可见的目标像素，其深度应在声明容差内一致。
4. 每个渲染文件名均有 pose；旋转是刚体旋转，帧 ID 正确，重投影误差小于声明阈值。
5. 每个 H5 通过 `lfnr-h5-v2` schema 检查，属性中的源 GT/OCC 路径、中心帧和内参
   与该 key 的 manifest 一致。
6. 受控静态轨迹重跑应保留标定指标。随机生产轨迹无需重放 seed，但必须保存实际的
   Sequence、FBX 与 manifest。

## 对旧报告的处理结论

* 已移除“随机轨迹不可复现”这一缺陷：随机性是这里的设计目标；要求的是溯源，不是
  强制确定性重放。
* 已确认：GT 是隐藏指定 `SM_` 前景遮挡物的受控对照；不将 MRQ 可见性或该 GT
  语义本身列为问题。
* 已修复：`rot_arc` pose 类别遗漏、当前图像与历史 FBX 的地图名模糊匹配，以及
  启动器配置/socket 端口未传入 UE。
* 仍需优先处理：缺少 UE 帧级 manifest、坐标缩放标定，以及遗留常量平面 H5 脚本
  被误用；它们仍会威胁 OCC/GT 与多轨迹对比。
* 降低相对优先级：socket、崩溃处理等通用调度问题仍需修复，但在本任务中主要风险是
  它们会让不完整或身份不明的数据被发布。
