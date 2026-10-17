# 轨迹 Canonical Pose 与合成一致性审计

## 范围与判定口径

本审计覆盖五类训练轨迹：`fix_line`、`rot_arc`、`plane_grid`、
`rot_spiral` 和 `rand_shell`。目标是让不同轨迹用于遮挡对比时，使用同一张
canonical GT 视图：相机在目标正上方、垂直向下观察目标，且图像方向一致。

本问题只检查 **UE/MRQ 原始渲染 pose**。H5 导出阶段对各轨迹单独重心化 pose
是下游参考视图的归一化，不要求跨轨迹相同；只要原始 canonical GT RGB、深度和
内参一致，就不影响遮挡效果对比。

当前配置有多个 `camera_height` 和 `plane_angle`。因此“同一个 pose”的合理比较
键是：

```text
(map, target, camera_height, plane_angle)
```

不同高度本就不是同一 pose；不同 `plane_angle` 目前也会使用不同的顶视 yaw，
图像会发生平面内旋转。若实验要求跨角度也共用同一 GT 图像，应将 canonical yaw
固定为常量（例如 `0`），并只将 `plane_angle` 用于旋转非中心参考轨迹。

## 当前 canonical 帧

默认 `num_frames=33` 时，各轨迹的预期 canonical 帧如下。

| 轨迹 | canonical index | 位置与 pitch | yaw | 结论 |
| --- | ---: | --- | --- | --- |
| `fix_line` | 16 | `(target_x, target_y, target_z + height)`，`-90` | 默认 33 帧时从前帧继承，等价于 `plane_angle` | 结果正确但实现脆弱 |
| `plane_grid` | 16 | 正上方，`-90` | `plane_angle` | 正确 |
| `rot_arc` | 16 | 正上方，`-90` | 从前帧继承，实际为 `plane_angle + 180` | **错误** |
| `rot_spiral` | 32 | 正上方，`-90` | `plane_angle` | 正确 |
| `rand_shell` | 32 | 正上方，`-90` | `plane_angle` | 正确 |

`rot_arc` 在顶点奇点处复用了圆弧进入顶点前的朝向。对于圆弧前半段，镜头从
`plane_angle` 方向向目标看，前帧 yaw 为 `plane_angle + 180`；正上方画面因此相对
其他四类轨迹旋转 180 度。位置和俯仰相同并不能消除该图像方向差异。

## 必须修复的原始渲染问题

### P0 - 圆弧 canonical yaw 不一致

在 `utils_sequencer.rot_arc` 的顶视奇点分支中，将：

```python
yaw = previous_yaw
```

改为：

```python
yaw = float(plane_angle_degrees)
```

这样同一 `(target, height, plane_angle)` 下，五类轨迹的 canonical 位姿均为：

```text
location = (target_x, target_y, target_z + camera_height)
rotation = (roll=0, pitch=-90, yaw=plane_angle)
```

### P1 - 直线 canonical yaw 不应依赖前帧

`fix_line` 默认 33 帧时，第 16 帧的前一帧朝向恰好产生正确 yaw；但这是对帧数和
路径方向的隐式依赖。应在顶视奇点分支直接设置：

```python
yaw = float(angle_degrees)
```

并在生成时要求 canonical 中心帧存在。对于中心 canonical 设计，`num_frames` 应
保持奇数；当前默认值 33 符合要求。

## 中心帧元数据

下游 `warp_utils.load_pose_info` 目前按目录名推断中心帧：`rot_spiral`、
`rand_shell` 取最后一帧，其余取中间帧。这与当前五类轨迹和 33 帧配置相符，
但属于脆弱约定。

建议在 Blender 生成的 `transforms.json` 中显式写入：

```json
{
  "canonical_frame_index": 16,
  "canonical_frame_stem": "0016"
}
```

螺旋和随机轨迹则写入 index 32。H5 转换器应优先读取这个字段，缺失时失败或仅在
兼容模式下回退到路径规则。这样改动帧数、文件命名或未来轨迹时不会静默选择错误
GT 帧。

## 轨迹合成质量

### `plane_grid`

中心帧被强制插入第 16 帧，保证 canonical pose 正确；但蛇形路径在进入和离开该
中心帧时可能发生较大跳跃。因此它是“以中心 GT 为锚点的视图集合”，不一定是严格
连续的相机运动轨迹。

### `rot_spiral`

当前实现实际使用 Fibonacci 球冠采样，`num_turns` 没有参与计算；名称“螺旋”与
几何实现不完全一致。若目标是覆盖不同观测方向，这个采样是合理的；若目标是连续
的指定圈数螺旋运动，应按 `num_turns` 重写参数化。

### `rand_shell`

随机性是设计目标，末帧也被显式固定为 canonical GT pose。不过 Catmull-Rom 样条
可能在控制点之间越出采样球壳/锥角约束，且最后一个随机点到 canonical 帧可能有
跳跃。对静态多视图采样这不一定有害；若需要物理连续的相机路径，应在样条上重投影
到允许球壳，并以 canonical 位置作为末端锚点进行平滑过渡。

## 验收实验

选定一个静态地图、一个 `Target_`、固定 `camera_height` 与一个 `plane_angle`，分别
生成五类轨迹。MRQ 渲染前读取 Sequence 的 canonical key，并断言：

1. canonical 帧索引分别为 16、16、16、32、32；
2. 五个位置在厘米级容差内相等，均为目标中心加 `camera_height`；
3. 五个旋转均为 `(0, -90, plane_angle)`；
4. GT RGB、GT depth、分辨率、`K` 和 canonical 文件 stem 完全相同；
5. OCC 仅在指定 `SM_` 前景遮挡物所在区域与 GT 不同。

若选择固定全局 canonical yaw 的实验设计，则第 3 条中的 `plane_angle` 替换为固定
值，并应跨三个 `plane_angle` 重复比较第 4 条。
