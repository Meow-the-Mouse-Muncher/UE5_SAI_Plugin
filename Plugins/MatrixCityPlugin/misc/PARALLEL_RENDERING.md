# 森林终版数据渲染

完整地图、采样预算、输出和恢复说明见项目根目录 FOREST_DATASET.md。

在user1的项目根目录执行：

    python3 Plugins/MatrixCityPlugin/misc/render_dataset.py --split train --gpu-ids 1 --workers-per-gpu 1 --sync-profile balanced

--split test只选择独立源场景scene_010；--dry-run只检查计划及素材。调度器读取Config/Dataset下已批准的CSV，不扩展Target/高度/方向的笛卡尔积。使用--maps时请填写/Game/Map/dataset/train/或test/下的完整包路径。

每条轨迹25视角，OCC和GT保留实体草、地形和石块，仅切换清单中的原树。原有GPU UUID/Vulkan匹配、端口和序列隔离、普通fence深度读回设置保留。先用单worker；旧场景的双worker显存预算不适用于当前地被规模。

复用相同--run-dir、输出及参数并加--resume可恢复；每个通道的完整帧号都必须通过检查。素材/计划改变会阻止误用旧完成标记。
