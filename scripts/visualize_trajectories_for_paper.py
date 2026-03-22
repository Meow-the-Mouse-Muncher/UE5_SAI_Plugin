#!/usr/bin/env python3
"""Visualize camera trajectories for supplementary material figures.

This script reproduces the geometry of five trajectories used in the UE pipeline:
- fix_line
- planar_grid
- rot_arc
- rot_spiral (Fibonacci cap + forced top-down last frame)
- rand_shell (random shell walk + forced top-down last frame)

Outputs publication-friendly PNGs:
1) One PNG per trajectory (3D only)
2) A combined 3D panel for side-by-side comparison

Example:
    python scripts/visualize_trajectories_for_paper.py \
        --output-dir ./scripts/trajectory_figs \
        --num-frames 33 \
        --trajectory-size 3500 \
        --height 8000 \
        --plane-angle 0 \
        --arc-angle 90 \
        --seed 42
"""

from __future__ import annotations

import argparse
import math
from pathlib import Path
from typing import Dict, List, Tuple

import matplotlib.pyplot as plt
from matplotlib import cm, colors
import numpy as np
from mpl_toolkits.mplot3d.art3d import Line3DCollection


ArrayN3 = np.ndarray
CM_TO_M = 0.01


def catmull_rom_spline(p0: np.ndarray, p1: np.ndarray, p2: np.ndarray, p3: np.ndarray, num_points: int) -> List[np.ndarray]:
    """Compute one Catmull-Rom spline segment."""
    curve: List[np.ndarray] = []
    for i in range(num_points):
        t = i / (num_points - 1) if num_points > 1 else 0.0
        t2 = t * t
        t3 = t2 * t
        point = 0.5 * (
            (-t3 + 2.0 * t2 - t) * p0
            + (3.0 * t3 - 5.0 * t2 + 2.0) * p1
            + (-3.0 * t3 + 4.0 * t2 + t) * p2
            + (t3 - t2) * p3
        )
        curve.append(point)
    return curve


def traj_fix_line(num_frames: int, trajectory_size: float, height: float, angle_deg: float) -> ArrayN3:
    angle = math.radians(angle_deg)
    dx, dy = math.cos(angle), math.sin(angle)
    half_len = trajectory_size / 2.0

    start = np.array([-half_len * dx, -half_len * dy, height], dtype=float)
    end = np.array([half_len * dx, half_len * dy, height], dtype=float)

    if num_frames <= 1:
        return start.reshape(1, 3)

    ts = np.linspace(0.0, 1.0, num_frames)
    pts = np.outer(1.0 - ts, start) + np.outer(ts, end)
    return pts


def traj_plane_grid(num_frames: int, trajectory_size: float, height: float, angle_deg: float) -> ArrayN3:
    cols, rows = 7, 5
    long_steps = max(cols - 1, rows - 1)
    step = trajectory_size / long_steps if long_steps > 0 else 0.0
    cx = (cols - 1) / 2.0
    cy = (rows - 1) / 2.0

    non_center_points: List[Tuple[float, float]] = []
    for rr in range(rows):
        r = rows - 1 - rr
        col_iter = range(cols) if rr % 2 == 0 else range(cols - 1, -1, -1)
        for c in col_iter:
            local_x = (c - cx) * step
            local_y = (r - cy) * step
            if abs(local_x) < 1e-9 and abs(local_y) < 1e-9:
                continue
            non_center_points.append((local_x, local_y))

    corner_a = (-3.0 * step, 2.0 * step)
    corner_b = (3.0 * step, -2.0 * step)
    selected = [
        p
        for p in non_center_points
        if not (
            (abs(p[0] - corner_a[0]) < 1e-6 and abs(p[1] - corner_a[1]) < 1e-6)
            or (abs(p[0] - corner_b[0]) < 1e-6 and abs(p[1] - corner_b[1]) < 1e-6)
        )
    ]

    needed_non_center = max(0, num_frames - 1)
    if len(selected) < needed_non_center:
        selected.extend([(0.0, 0.0)] * (needed_non_center - len(selected)))
    selected = selected[:needed_non_center]

    mid_index = num_frames // 2
    before = selected[:mid_index]
    after = selected[mid_index : mid_index + (num_frames - mid_index - 1)]
    ordered_xy = before + [(0.0, 0.0)] + after

    angle = math.radians(angle_deg)
    cos_a, sin_a = math.cos(angle), math.sin(angle)

    pts = np.zeros((len(ordered_xy), 3), dtype=float)
    for i, (lx, ly) in enumerate(ordered_xy):
        if i == mid_index:
            lx, ly = 0.0, 0.0
        x = lx * cos_a - ly * sin_a
        y = lx * sin_a + ly * cos_a
        pts[i] = (x, y, height)
    return pts


def traj_rot_arc(num_frames: int, radius: float, arc_angle_deg: float, plane_angle_deg: float) -> ArrayN3:
    arc = math.radians(arc_angle_deg)
    plane = math.radians(plane_angle_deg)

    center_angle = math.pi / 2.0
    start_angle = center_angle - arc / 2.0
    end_angle = center_angle + arc / 2.0

    ts = np.linspace(0.0, 1.0, max(1, num_frames))
    pts = np.zeros((len(ts), 3), dtype=float)
    for i, t in enumerate(ts):
        a = start_angle + t * (end_angle - start_angle)
        local_r = radius * math.cos(a)
        local_z = radius * math.sin(a)
        x = local_r * math.cos(plane)
        y = local_r * math.sin(plane)
        z = local_z
        pts[i] = (x, y, z)
    return pts


def traj_rot_spiral(num_frames: int, radius: float, arc_angle_deg: float, start_angle_deg: float) -> ArrayN3:
    # Matches UE logic: N-1 Fibonacci-cap points + final forced zenith point.
    n_spiral = max(0, num_frames - 1)
    phi_g = math.pi * (3.0 - math.sqrt(5.0))

    half_angle = arc_angle_deg / 2.0
    min_elev_deg = max(0.0, 90.0 - half_angle)
    z_min = radius * math.sin(math.radians(min_elev_deg))
    z_max = radius
    z_range = z_max - z_min

    pts: List[Tuple[float, float, float]] = []
    for i in range(n_spiral):
        lz = i / n_spiral if n_spiral > 0 else 0.0
        z = z_min + lz * z_range
        r_xy = math.sqrt(max(0.0, radius * radius - z * z))
        theta = phi_g * i + math.radians(start_angle_deg)
        x = r_xy * math.cos(theta)
        y = r_xy * math.sin(theta)
        pts.append((x, y, z))

    if num_frames > 0:
        pts.append((0.0, 0.0, radius))

    return np.asarray(pts, dtype=float)


def _distribute_frames(total_f: int, n_segs: int) -> List[int]:
    if n_segs == 0:
        return []
    base = total_f // n_segs
    rem = total_f % n_segs
    return [base + 1 if i < rem else base for i in range(n_segs)]


def traj_rand_shell(
    num_frames: int,
    min_radius: float,
    max_radius: float,
    arc_angle_deg: float,
    mid_height: float,
    seed: int,
) -> ArrayN3:
    rng = np.random.default_rng(seed)
    n_random = max(0, num_frames - 1)
    n_control = max(5, n_random // 6)

    max_theta = math.radians(arc_angle_deg / 2.0)
    min_cos = math.cos(max_theta)

    def sample_point() -> np.ndarray:
        r = rng.uniform(min_radius, max_radius)
        cos_theta = rng.uniform(min_cos, 1.0)
        theta = math.acos(cos_theta)
        phi = rng.uniform(0.0, 2.0 * math.pi)
        sin_theta = math.sin(theta)
        x = r * sin_theta * math.cos(phi)
        y = r * sin_theta * math.sin(phi)
        z = r * cos_theta
        return np.array([x, y, z], dtype=float)

    control_points = [sample_point() for _ in range(n_control)]
    n_seg = n_control - 1

    total_points_needed = n_random + (n_seg - 1)
    frames_per_segment = _distribute_frames(total_points_needed, n_seg)

    full_path: List[np.ndarray] = []
    cps = [control_points[0]] + control_points + [control_points[-1]]
    for i in range(n_seg):
        p0, p1, p2, p3 = cps[i], cps[i + 1], cps[i + 2], cps[i + 3]
        n_points = frames_per_segment[i]
        if n_points <= 0:
            continue
        seg = catmull_rom_spline(p0, p1, p2, p3, n_points)
        if i < n_seg - 1:
            seg = seg[:-1]
        full_path.extend(seg)

    full_path = full_path[:n_random]
    pts = np.asarray(full_path, dtype=float) if full_path else np.zeros((0, 3), dtype=float)

    if num_frames > 0:
        gt = np.array([[0.0, 0.0, mid_height]], dtype=float)
        pts = np.vstack([pts, gt])

    return pts


def compute_limits(trajs: Dict[str, ArrayN3], pad_ratio: float = 0.08) -> Tuple[Tuple[float, float], Tuple[float, float], Tuple[float, float]]:
    all_pts = np.concatenate([v for v in trajs.values() if len(v) > 0], axis=0)
    mins = all_pts.min(axis=0)
    maxs = all_pts.max(axis=0)
    spans = np.maximum(maxs - mins, 1.0)
    pad = spans * pad_ratio
    lo = mins - pad
    hi = maxs + pad
    return (lo[0], hi[0]), (lo[1], hi[1]), (lo[2], hi[2])


def set_equal_3d(ax: plt.Axes, pts: ArrayN3) -> None:
    x_min, y_min, z_min = pts.min(axis=0)
    x_max, y_max, z_max = pts.max(axis=0)
    x_span = x_max - x_min
    y_span = y_max - y_min
    z_span = z_max - z_min
    max_span = max(x_span, y_span, z_span, 1.0)

    x_mid = (x_max + x_min) * 0.5
    y_mid = (y_max + y_min) * 0.5
    z_mid = (z_max + z_min) * 0.5

    half = 0.5 * max_span
    ax.set_xlim(x_mid - half, x_mid + half)
    ax.set_ylim(y_mid - half, y_mid + half)
    ax.set_zlim(z_mid - half, z_mid + half)
    ax.set_box_aspect((1.0, 1.0, 1.0))


def draw_colored_path_3d(ax: plt.Axes, pts: ArrayN3, cmap: str = "viridis") -> None:
    if len(pts) < 2:
        return
    segs = np.stack([pts[:-1], pts[1:]], axis=1)
    cvals = np.linspace(0.0, 1.0, len(segs))
    lc = Line3DCollection(segs, cmap=cmap, linewidth=2.2, alpha=0.95)
    lc.set_array(cvals)
    ax.add_collection3d(lc)


def draw_common_markers_3d(ax: plt.Axes, pts: ArrayN3) -> None:
    if len(pts) == 0:
        return
    ax.scatter(pts[0, 0], pts[0, 1], pts[0, 2], s=42, marker="o", color="#2ca02c", label="Start", zorder=6)
    ax.scatter(pts[-1, 0], pts[-1, 1], pts[-1, 2], s=42, marker="s", color="#d62728", label="End", zorder=6)


def draw_common_markers_xy(ax: plt.Axes, pts: ArrayN3) -> None:
    if len(pts) == 0:
        return
    ax.scatter(pts[0, 0], pts[0, 1], s=34, marker="o", color="#2ca02c", zorder=6)
    ax.scatter(pts[-1, 0], pts[-1, 1], s=34, marker="s", color="#d62728", zorder=6)


def style_3d_axes(ax: plt.Axes) -> None:
    """Use a clean white background with subtle grid lines and tight panes."""
    ax.set_facecolor("white")
    ax.grid(True, color="#dddddd", linewidth=0.45, alpha=0.45)
    
    # 隐藏 3D 灰色背景墙壁，只保留网格线，让画面更干净紧凑
    ax.xaxis.set_pane_color((1.0, 1.0, 1.0, 0.0))
    ax.yaxis.set_pane_color((1.0, 1.0, 1.0, 0.0))
    ax.zaxis.set_pane_color((1.0, 1.0, 1.0, 0.0))
    
    # 隐藏坐标轴边框线（仅留刻度和网格）
    ax.xaxis.line.set_color((1.0, 1.0, 1.0, 0.0))
    ax.yaxis.line.set_color((1.0, 1.0, 1.0, 0.0))
    ax.zaxis.line.set_color((1.0, 1.0, 1.0, 0.0))


def style_2d_axes(ax: plt.Axes) -> None:
    """Use minimalist axis and soft grid style for paper figures."""
    ax.set_facecolor("white")
    ax.grid(True, color="#dddddd", linewidth=0.45, alpha=0.45)
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)
    ax.spines["left"].set_color("#888888")
    ax.spines["bottom"].set_color("#888888")
    ax.spines["left"].set_linewidth(0.7)
    ax.spines["bottom"].set_linewidth(0.7)


def draw_sample_points_3d(ax: plt.Axes, pts: ArrayN3) -> None:
    """Draw all sampled camera centers explicitly in 3D."""
    if len(pts) == 0:
        return
    cvals = np.linspace(0.0, 1.0, len(pts))
    ax.scatter(
        pts[:, 0],
        pts[:, 1],
        pts[:, 2],
        c=cvals,
        cmap="viridis",
        s=18,
        alpha=0.9,
        edgecolors="white",
        linewidths=0.35,
        zorder=7,
    )


def draw_sample_points_2d(ax: plt.Axes, pts: ArrayN3) -> None:
    """Draw all sampled camera centers explicitly in top-down view."""
    if len(pts) == 0:
        return
    cvals = np.linspace(0.0, 1.0, len(pts))
    ax.scatter(
        pts[:, 0],
        pts[:, 1],
        c=cvals,
        cmap="viridis",
        s=22,
        alpha=0.95,
        edgecolors="white",
        linewidths=0.35,
        zorder=6,
    )


def is_irregular_sampling(name: str) -> bool:
    """Trajectories that are better shown as sampling sets instead of dense polylines."""
    return name in {"rand_shell"}


def draw_sparse_arrows_2d(ax: plt.Axes, pts: ArrayN3, color: str = "#5a5a5a") -> None:
    """Draw sparse temporal arrows to indicate progression without clutter."""
    if len(pts) < 2:
        return
    step = max(1, len(pts) // 8)
    idxs = list(range(0, len(pts) - 1, step))
    for i in idxs:
        p0 = pts[i, :2]
        p1 = pts[i + 1, :2]
        d = p1 - p0
        ax.arrow(
            p0[0],
            p0[1],
            d[0] * 0.8,
            d[1] * 0.8,
            width=0.0,
            head_width=max(15.0, 0.012 * max(np.ptp(pts[:, 0]), np.ptp(pts[:, 1]), 1.0)),
            head_length=max(25.0, 0.02 * max(np.ptp(pts[:, 0]), np.ptp(pts[:, 1]), 1.0)),
            length_includes_head=True,
            color=color,
            alpha=0.45,
            zorder=3,
        )


def smooth_path_catmull_rom(points: ArrayN3, samples_per_segment: int = 8) -> ArrayN3:
    """Smooth a 3D polyline with Catmull-Rom while preserving endpoint order."""
    n = len(points)
    if n < 3 or samples_per_segment <= 1:
        return points

    out: List[np.ndarray] = []
    for i in range(n - 1):
        p0 = points[i - 1] if i - 1 >= 0 else points[i]
        p1 = points[i]
        p2 = points[i + 1]
        p3 = points[i + 2] if i + 2 < n else points[i + 1]

        seg = catmull_rom_spline(p0, p1, p2, p3, samples_per_segment)
        if i < n - 2:
            seg = seg[:-1]
        out.extend(seg)

    return np.asarray(out, dtype=float)


def smooth_spiral_through_points(points: ArrayN3, samples_per_segment: int = 18) -> ArrayN3:
    """Smooth rot_spiral in cylindrical space while passing through all true samples.

    The curve is built in temporal order from (theta, radius, z) with theta unwrapped,
    then mapped back to (x, y, z). This keeps all sample points on the curve and
    preserves the true multi-turn Fibonacci progression.
    """
    n = len(points)
    if n < 3:
        return points

    x = points[:, 0]
    y = points[:, 1]
    z = points[:, 2]
    r = np.sqrt(x * x + y * y)
    theta = np.unwrap(np.arctan2(y, x))

    trz = np.stack([theta, r, z], axis=1)
    trz_smooth = smooth_path_catmull_rom(trz, samples_per_segment=samples_per_segment)

    theta_s = trz_smooth[:, 0]
    r_s = np.maximum(trz_smooth[:, 1], 0.0)
    z_s = trz_smooth[:, 2]

    out = np.zeros_like(trz_smooth)
    out[:, 0] = r_s * np.cos(theta_s)
    out[:, 1] = r_s * np.sin(theta_s)
    out[:, 2] = z_s
    return out


def get_display_path(name: str, pts: ArrayN3) -> ArrayN3:
    if name == "rot_spiral":
        # Smooth in temporal order and pass through real samples.
        # Keep the final GT frame unsmoothed and attached at the end.
        if len(pts) <= 3:
            return pts
        core = pts[:-1]
        gt = pts[-1:]
        core_smooth = smooth_spiral_through_points(core, samples_per_segment=18)
        return np.vstack([core_smooth, gt])
    return pts


def plot_single_trajectory(name: str, pts: ArrayN3, out_file: Path, dpi: int) -> None:
    display_pts = get_display_path(name, pts)
    pts_plot = pts * CM_TO_M
    display_plot = display_pts * CM_TO_M

    # 使用更紧凑的 5x5 正方形画布
    fig = plt.figure(figsize=(5.0, 5.0))
    ax3d = fig.add_subplot(111, projection="3d")
    
    draw_colored_path_3d(ax3d, display_plot)
    draw_sample_points_3d(ax3d, pts_plot)
    draw_common_markers_3d(ax3d, pts_plot)
    set_equal_3d(ax3d, display_plot)
    
    ax3d.view_init(elev=23, azim=-58)
    
    # 保持 X 和 Y 向内收缩，但把 Z 轴的 labelpad 改为正数 (例如 2 或 5)
    ax3d.set_xlabel("X (m)", labelpad=0)
    ax3d.set_ylabel("Y (m)", labelpad=0)
    ax3d.set_zlabel("Z (m)", labelpad=4)  # <--- 修改这里
    
    # 将刻度数字也向内收缩
    ax3d.tick_params(axis='x', pad=0)
    ax3d.tick_params(axis='y', pad=0)
    ax3d.tick_params(axis='z', pad=2)
    
    style_3d_axes(ax3d)

    # 增加 pad_inches，给 Z 轴标签留出安全空间
    fig.subplots_adjust(left=0.03, right=0.90, top=0.95, bottom=0.05)
    fig.savefig(out_file, dpi=dpi, bbox_inches="tight", pad_inches=0.25)
    plt.close(fig)

def plot_panel(trajs: Dict[str, ArrayN3], out_file: Path, dpi: int) -> None:
    names = list(trajs.keys())
    n = len(names)
    
    # 缩小单个子图的分配宽度 (从 3.7 降到 3.2)，让它们靠得更紧
    fig = plt.figure(figsize=(3.2 * n, 3.8))
    # 核心修改：明确设置 wspace=0.0 甚至负数，挤压子图间距
    gs = fig.add_gridspec(1, n, wspace=0.0)

    for col, name in enumerate(names):
        pts = trajs[name]
        display_pts = get_display_path(name, pts)
        pts_plot = pts * CM_TO_M
        display_plot = display_pts * CM_TO_M

        ax3d = fig.add_subplot(gs[0, col], projection="3d")
        draw_colored_path_3d(ax3d, display_plot)
        draw_sample_points_3d(ax3d, pts_plot)
        draw_common_markers_3d(ax3d, pts_plot)
        set_equal_3d(ax3d, display_plot)
        
        ax3d.view_init(elev=23, azim=-58)
        
        # 面板图的标签设置
        ax3d.set_xlabel("X (m)", labelpad=0)
        ax3d.set_ylabel("Y (m)", labelpad=0)
        ax3d.set_zlabel("Z (m)", labelpad=6) 
        ax3d.tick_params(axis='x', pad=0)
        ax3d.tick_params(axis='y', pad=0)
        ax3d.tick_params(axis='z', pad=2)
        
        style_3d_axes(ax3d)

    fig.subplots_adjust(left=0.01, right=0.98, top=0.98, bottom=0.02, wspace=0.1)
    
    # 面板图保存时也增加 pad_inches
    fig.savefig(out_file, dpi=dpi, bbox_inches="tight", pad_inches=0.2)
    plt.close(fig)


def build_trajectories(args: argparse.Namespace) -> Dict[str, ArrayN3]:
    return {
        "fix_line": traj_fix_line(
            num_frames=args.num_frames,
            trajectory_size=args.trajectory_size,
            height=args.height,
            angle_deg=args.plane_angle,
        ),
        "planar_grid": traj_plane_grid(
            num_frames=args.num_frames,
            trajectory_size=args.trajectory_size,
            height=args.height,
            angle_deg=args.plane_angle,
        ),
        "rot_arc": traj_rot_arc(
            num_frames=args.num_frames,
            radius=args.height,
            arc_angle_deg=args.arc_angle,
            plane_angle_deg=args.plane_angle,
        ),
        "rot_spiral": traj_rot_spiral(
            num_frames=args.num_frames,
            radius=args.height,
            arc_angle_deg=args.arc_angle,
            start_angle_deg=args.plane_angle,
        ),
        "rand_shell": traj_rand_shell(
            num_frames=args.num_frames,
            min_radius=args.min_radius,
            max_radius=args.max_radius,
            arc_angle_deg=args.arc_angle,
            mid_height=args.height,
            seed=args.seed,
        ),
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Visualize five camera trajectories as publication-ready PNGs.")
    parser.add_argument("--output-dir", type=str, default="scripts/trajectory_figs", help="Directory to save PNGs")
    parser.add_argument("--num-frames", type=int, default=33, help="Number of frames per trajectory")
    parser.add_argument("--trajectory-size", type=float, default=3500.0, help="Planar trajectory size (UE cm)")
    parser.add_argument("--height", type=float, default=8000.0, help="Reference camera height / radius (UE cm)")
    parser.add_argument("--plane-angle", type=float, default=0.0, help="In-plane angle in degrees")
    parser.add_argument("--arc-angle", type=float, default=90.0, help="Arc / cone angle in degrees")
    parser.add_argument("--min-radius", type=float, default=5000.0, help="Min shell radius for rand_shell")
    parser.add_argument("--max-radius", type=float, default=12000.0, help="Max shell radius for rand_shell")
    parser.add_argument("--seed", type=int, default=42, help="Random seed for rand_shell")
    parser.add_argument("--dpi", type=int, default=400, help="PNG dpi (300-600 recommended for papers)")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    out_dir = Path(args.output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    # Paper-oriented defaults: clean style, colorblind-friendly sequential colormap.
    plt.rcParams.update(
        {
            "figure.facecolor": "white",
            "axes.facecolor": "white",
            "savefig.facecolor": "white",
            "font.size": 11,
            "axes.titlesize": 12,
            "axes.labelsize": 10,
            "legend.fontsize": 9,
            "xtick.labelsize": 9,
            "ytick.labelsize": 9,
            "lines.linewidth": 1.8,
        }
    )

    trajs = build_trajectories(args)

    for name, pts in trajs.items():
        out_file = out_dir / f"{name}.png"
        plot_single_trajectory(name, pts, out_file, dpi=args.dpi)

    panel_file = out_dir / "trajectories_panel.png"
    plot_panel(trajs, panel_file, dpi=args.dpi)

    print(f"Saved {len(trajs)} single-trajectory figures + 1 panel to: {out_dir}")
    print("Tip: Use --dpi 600 for camera-ready supplementary figures.")


if __name__ == "__main__":
    main()
