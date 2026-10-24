# 相机位姿与内参 JSON 导出

本流程只读取已经生成的 FBX，向对应序列的 `pose/transforms.json` 写入相机元数据。它不会启动 UE，不会渲染图像，也不会生成或训练新数据。

目标服务器的项目与运行时：

```text
项目        /mnt/nas_b/sjy/code/PCGBiomeForestPoplar
Python      /mnt/nas_b/sjy/envs/baselines/bin/python
Blender     /mnt/nas_b/sjy/tools/blender/5.2.2/blender
方法仓库    /mnt/nas_b/sjy/code/Mult_view_Recon
```

## 选择已有数据

推荐通过本次生成任务的 `run/plan.json` 定位输出目录：

```bash
cd /mnt/nas_b/sjy/code/PCGBiomeForestPoplar
/mnt/nas_b/sjy/envs/baselines/bin/python scripts/export_camera_json.py \
  --run-dir Saved/Migration/smoke/five_trajectories/run \
  --limit 1 --dry-run
```

`--run-dir` 指向包含 `plan.json` 的运行目录。脚本要求计划声明原生 `Resolution: [512, 512]`、`num_frames: 25`，并读取计划中的绝对 `output_root`。

也可以直接指定已渲染的数据根目录；此时会直接核对 RGB、深度、FBX 文件，而不依赖运行计划：

```bash
/mnt/nas_b/sjy/envs/baselines/bin/python scripts/export_camera_json.py \
  --output-root /mnt/cpfs_b5/perception_data/sjy/forest_dataset/train \
  --trajectory plane_grid \
  --sequence scene_010_Target_001_height_050_ang_000 \
  --limit 1 --dry-run
```

`--run-dir` 与 `--output-root` 必须二选一。`--trajectory`、`--sequence` 可重复指定；`--limit` 限制实际选中的序列数。`--dry-run` 只校验已有数据并打印计划，不启动 Blender、不写 JSON 或报告。确认输出计划后，删除 `--dry-run` 即可导出所选序列。

新目录布局如下：

```text
<output_root>/
  Exported_FBX/<trajectory>/<prefix>.fbx
  <trajectory>/<prefix>_GT/
    rgb/0000.png ... 0024.png
    depth/0000.exr ... 0024.exr
    pose/transforms.json          # 导出结果
  <trajectory>/<prefix>_occ/
    rgb/0000.png ... 0024.png
    depth/0000.exr ... 0024.exr
    pose/transforms.json          # 与 GT 的 JSON 内容相同
```

五种轨迹为 `fix_line`、`plane_grid`、`rot_arc`、`rot_spiral`、`rand_shell`。导出不检查 RGB/深度图像：图像完整性由渲染调度器负责，不完整的序列会被整体删除并重新渲染，之后才导出相机 JSON。单独调用本脚本时，调用方需保证图像已完整。

如需从其他位置读取 FBX，可显式添加 `--fbx-root /absolute/Exported_FBX`。该目录仍使用 `<trajectory>/<prefix>.fbx` 布局；相机 JSON 仍写入所选输出根目录的 GT/OCC 序列。

## 相机协议

JSON 的 `schema_version` 为 `camera-json-v1-25views`：

| 字段 | 含义 |
| --- | --- |
| `coordinate_convention` | 固定 `opencv_c2w`：相机到世界的 4×4 刚体矩阵，相机轴为右、下、前 |
| `translation_units` | 固定 `m`，矩阵平移为米 |
| `depth_units` | 固定 `cm`，声明现有 UE EXR 深度的单位；导出脚本不改写 EXR |
| `w`, `h` | 整数 `512`、`512` |
| `fl_x`, `fl_y`, `cx`, `cy` | 像素单位的相机内参，由 Blender 渲染投影矩阵计算，包含镜头偏移 |
| `intrinsics` | 对应的 3×3 内参矩阵 |
| `pixel_center_offset` | 固定 `0.5`，像素采样点位于像素中心 |
| `num_views` | 整数 `25` |
| `view_order`, `includes_occ_center` | 固定 `sequence`、`true` |
| `center_idx` | `fix_line/plane_grid/rot_arc` 为 `12`；`rot_spiral/rand_shell` 为 `24` |
| `frames` | 保留原渲染顺序；`frame_id` 为 `0..24`，图像路径为 `../rgb/0000.png..0024.png` |
| `source` | FBX 路径和 SHA-256、Blender 版本、动画采样与单位转换等来源信息 |

Blender 导入时读取 FBX 声明的单位，使用 `METRIC`、`scale_length=1` 的场景，因此平移以米表示。导出时只从旋转块中移除均匀缩放，并将相机 Y/Z 轴转换为 OpenCV 约定；不会给平移再除以 100，也不再固定将旋转乘以 100。剪切、非均匀缩放、反射、非有限矩阵会报错。

世界坐标沿用 Blender FBX importer 转换后的统一世界基底。这里的 `opencv_c2w` 指相机轴约定；不要把该矩阵当作未经转换的 UE 原始位姿。

采样保留源动画第 `0..24` 帧，不根据位姿是否变化筛帧。已确认 UE FBX 可能在第 `25` 帧附加重复末端 hold；只有该额外终点确实重复第 `24` 帧时才允许忽略。相机内参若随动画变化，会拒绝写成固定内参 JSON。

## 写入、运行时和报告

默认拒绝覆盖内容不同的已有 JSON；相同内容可重复运行。需要替换时显式加 `--overwrite`。脚本会先检查 GT/OCC 两个目标，再原子写入；本次导出不会创建原文件备份。

Blender 选择顺序为 `--blender`、`BLENDER_BIN`、项目旁唯一可用的 `tools/blender/*/blender`、最后系统 `PATH`。需要固定版本时明确指定：

```bash
/mnt/nas_b/sjy/envs/baselines/bin/python scripts/export_camera_json.py \
  --run-dir Saved/Migration/smoke/five_trajectories/run \
  --trajectory plane_grid --limit 1 \
  --blender /mnt/nas_b/sjy/tools/blender/5.2.2/blender
```

Blender 使用自己的 Python。启动器会去除继承的 `PYTHONPATH`、`PYTHONHOME`，避免其他项目的模块影响导入。

使用 `--run-dir` 时，报告默认写入 `<run-dir>/camera_export_report.json`；使用 `--output-root` 时，默认写入 `<output_root>/metadata/camera_export_report.json`。可用 `--report /absolute/report.json` 指定位置。报告记录每个序列、输出路径、JSON SHA-256 与源 FBX SHA-256。

## 方法端读取

`Mult_view_Recon` 使用 `utils/camera_json.py` 统一读取和校验上述协议：

```python
from utils.camera_json import load_camera_json

camera = load_camera_json(
    "/absolute/output/plane_grid/scene_010_Target_001_height_050_ang_000_occ"
)
K = camera["K"]
poses_c2w_m = camera["poses"]
center_idx = camera["center_idx"]
```

新 JSON 已经是 OpenCV 相机轴，方法端无需再次翻转 Y/Z；平移已经是米，原始厘米深度转换为米后再与位姿使用同一尺度。转换为 LFNR H5 时，位姿与深度再共同应用既定归一化尺度。Declutter-NeRF 使用统一适配器完成后续射线坐标转换。

## 实际几何校验

`scripts/forest_dataset/validate_geometry.py` 在已有的审计目录中读取 `reproduce/selection.json` 和 `reproduce/cameras.json`，校验实际 RGB/EXR 与相机几何，并生成离线 `index.html`、`evidence/metrics.json`、输入 SHA-256 和特征对应点。完整 CPU 依赖见根目录 `requirements-dev.txt`。

```bash
python3 /mnt/nas_b/sjy/code/PCGBiomeForestPoplar/scripts/forest_dataset/validate_geometry.py \
  --audit-dir /mnt/nas_b/sjy/code/PCGBiomeForestPoplar/Saved/GeometryValidation/20261021_054538
```

审计目录的 `reproduce/` 保存 Blender 提取脚本、抽样配方、合并相机记录和字体，`evidence/` 保存指标、输入哈希、对应点和复核图。审计副本与报告不修改生产输出。仅重新生成 HTML 可追加 `--html-only`；显示数据直接从现有 HTML 读取，不另存重复的 `visuals.json`。

HTML 的三维图旁提供场景选择、直线/网格/圆弧/螺旋/随机球壳五类轨迹按钮，以及俯视/侧视/斜视。轨迹切换同步更新点云、RGB 和残差；只有直线的补充环境样本会明确标注，完整五类比较请选择正式样本或已有 JSON 验证样本。

三维图默认显示所选轨迹的全部 25 个相机和全部 25 帧 GT 点云。每帧按 16 像素间隔取点，共 25,600 点。页面显示当前帧数和点数。展开“筛选点云帧”后，可单独选择任意帧。相机轨迹始终保留全部帧。点云也可按帧着色。

旧报告若只包含部分帧的点云，可在 `--html-only` 后追加 `--refresh-pointclouds`。脚本重新读取全部 GT RGB 和深度。脚本先核对原始输入哈希，再更新内嵌点云。该操作不修改数值检查结果或原始数据。

校验包含逐帧 UE 轨迹到 OpenCV 的独立轴/单位转换、原始 FBX 焦距与胶片尺寸、多视角 RGB-D 重投影、图像特征匹配，以及错误单位、坐标轴、矩阵方向、焦距、深度定义和帧错位对照。图像特征帧对至少需要 12 个匹配点才参与汇总，深度可见掩码不使用 RGB 误差。

绝对尺度还需依靠已批准的 UE 轨迹和目标位置：如果 pose 平移和深度同时乘同一个比例，多视角重投影仍会一致。EXR 实际以 HALF 存储，50 米附近间隔约 4 厘米，120 米附近约 8 厘米；不能据此声明毫米级深度。树叶、透明物和遮挡变化会使 OCC 共同可见支持变少，报告分别展示 GT 与 OCC 的覆盖范围。
