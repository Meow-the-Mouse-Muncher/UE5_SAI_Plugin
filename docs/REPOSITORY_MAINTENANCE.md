# 仓库维护规范

仓库维护 UE5.6 森林数据采集链路：按正式清单生成 OCC/GT 配对图像与 FBX，并导出 OpenCV 相机 JSON。源码、插件资源、采集配方和回归测试一起版本管理。

## 维护范围

| 内容 | 作用 |
| --- | --- |
| 插件 `Source/`、`Content/Python/`、`.uplugin` | 渲染同步、轨迹、MRQ、取消、精确 FBX 导出 |
| 插件蓝图、材质、UI 和图标 | 保留 UE 资产引用和插件资源 |
| 插件 `misc/` 当前调度模块 | GPU 分配、独立 worker、恢复、锁和性能采样 |
| 根目录 `Source/`、`.uproject`、`Config/DefaultEngine.ini` | 项目模块构建和渲染配置 |
| `Config/Dataset/` | 地图、目标、素材哈希及批准的 train/test 序列 |
| `scripts/render_forest_dataset.py` 和 `.sh` | 持久任务入口和服务器续跑包装器 |
| 相机导出脚本与 `camera_json_protocol.py` | 已有 RGB/深度/FBX 的相机元数据导出与验证 |
| `scripts/engine_patches/` | 正式 UE5.6.1 修复和对应构建记录 |
| 三处 CPU 测试、配置模板、依赖和当前操作文档 | 保持构建、配置和协议可验证 |

插件模块和资产路径保留 `MatrixCityPlugin` 名称，避免破坏 UE 类名和资产引用。`Config/Dataset/` 与引擎补丁保留原始字节；不要为排版而改写校验对象。

## 已删除的历史内容

| 内容 | 当前替代方式 |
| --- | --- |
| `scripts/align_pose_to_center_*.py`、`analyze_poses.py`、`generate_transforms_3dgs.py` | 当前相机导出统一使用 `export_camera_json.py`；下游重聚焦不再由此仓库维护 |
| `merge_aerial_street.py`、`rgbd2pc.py`、`vis_depth.py`、`visualize_trajectories_for_paper.py` | 移除与当前采集无关的转换、可视化和历史轨迹副本 |
| `clean_renders.py`、`ue5_export_sequences_to_fbx.py`、`Trans_3Dgs_Data.md`、`pose.blend` | 正式 worker 自动导出本轮序列，保留完整 25 帧协议 |
| 插件 `misc/run_export_only.py`、`run_init.py`、`config.py` | 使用当前入口和部署说明；UE 运行时仍使用 `Content/Python/data/config.py` |
| 插件 `misc/email_sender.py`、`visualize.py`、`vis_exr.py` | 移除未接入当前调度器的通知和可视化模块 |
| `Content/Python/pipeline_export.py`、`utils_export.py` | 使用正式 worker 的精确 FBX 导出 |
| 旧架构、审计、相关资源、Sphinx 页面、构建配置和配图 | 使用 README、快速开始、当前流程与相机协议文档 |
| 旧 `.gitlab-ci.yml` 与本地 `.debug`、`.sym` | 移除旧发布任务和不需要的符号产物 |

删除前的材料和原始暂存内容保存在本地 `.git/cleanup-backups/20261021/`，不随代码推送；已提交的历史工具也可从 Git 历史恢复。

## 本机部署文件

`misc/user.json` 使用本机路径、用户名和驱动环境，不进入版本管理；首次部署从 `misc/user.example.json` 复制。当前运行所需的 `.so` 和 `UnrealEditor.modules` 保留在本机 `Binaries/`，新部署从源码构建或单独分发匹配引擎的二进制。

项目 `Content/`、缓存、日志、渲染输出和运行 checkpoint 留在仓库外。迁移或恢复采集时须另外保留完整 session 与输出，不能仅靠克隆代码恢复运行状态。

## 提交与验证

从根目录运行 `python3 -m pytest -q`，并检查 `git diff --cached --check`。修改轨迹、采样、遮挡、FBX 或引擎同步时，再在 UE 环境执行小规模渲染并验收输出；CPU 测试不能替代 GPU 渲染验收。

提交前确认没有本机配置、凭据、编译产物或数据输出。正式清单和配方变化须连同验收记录一起评估。推送 `Fixed_Track` 前先 fetch 并确认远程是本地祖先，使用正常快进推送。
