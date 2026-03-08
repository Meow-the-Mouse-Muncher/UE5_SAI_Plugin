"""
Convert depth maps to visualization PNG images.

Sequence traversal and path style are aligned with align_pose_to_center_inv_h5.py.
"""

import os
import numpy as np

os.environ["OPENCV_IO_ENABLE_OPENEXR"] = "1"
import cv2
from tqdm import tqdm


def load_depth(depth_path):
	"""Read depth map as float/uint from EXR/PNG."""
	depth = cv2.imread(depth_path, cv2.IMREAD_ANYCOLOR | cv2.IMREAD_ANYDEPTH)
	if depth is None:
		return None
	if depth.ndim == 3:
		depth = depth[..., 0]
	return depth


def depth_to_vis_png(depth):
	"""Convert depth to 8-bit grayscale visualization using robust range."""
	depth = depth.astype(np.float32)
	valid = np.isfinite(depth) & (depth > 0)

	vis = np.zeros_like(depth, dtype=np.uint8)
	if not np.any(valid):
		return vis

	p1, p99 = np.percentile(depth[valid], [1, 99])
	if p99 <= p1:
		p99 = p1 + 1e-6

	norm = (depth - p1) / (p99 - p1)
	norm = np.clip(norm, 0.0, 1.0)
	vis = (norm * 255.0).astype(np.uint8)
	return vis


def find_gt_depth_file(gt_depth_dir):
	"""Pick one depth file from GT depth folder (middle frame if multiple)."""
	if not os.path.isdir(gt_depth_dir):
		return None

	depth_files = sorted(
		[
			os.path.join(gt_depth_dir, f)
			for f in os.listdir(gt_depth_dir)
			if f.lower().endswith(".exr") or f.lower().endswith(".png")
		]
	)

	if not depth_files:
		return None

	# Use middle frame for compatibility with existing center-frame convention.
	return depth_files[len(depth_files) // 2]


def batch_convert(base_dir, output_base, trajectories=None):
	sequence_tasks = []

	if not os.path.exists(base_dir):
		print(f"Base dir {base_dir} does not exist")
		return

	if trajectories is None:
		# 默认处理 base_dir 下所有轨迹子文件夹
		trajectories = [
			d for d in os.listdir(base_dir)
			if os.path.isdir(os.path.join(base_dir, d))
		]

	for trajectory_type in trajectories:
		trajectory_dir = os.path.join(base_dir, trajectory_type)
		if not os.path.isdir(trajectory_dir):
			continue

		trajectory_out_dir = os.path.join(output_base, trajectory_type)
		os.makedirs(trajectory_out_dir, exist_ok=True)

		for sequence_name in os.listdir(trajectory_dir):
			if not sequence_name.endswith("_GT"):
				continue

			gt_sequence_dir = os.path.join(trajectory_dir, sequence_name)
			if not os.path.isdir(gt_sequence_dir):
				continue

			gt_depth_dir = os.path.join(gt_sequence_dir, "depth")
			depth_path = find_gt_depth_file(gt_depth_dir)
			if depth_path is None:
				continue

			out_path = os.path.join(trajectory_out_dir, f"{sequence_name}.png")
			sequence_tasks.append((depth_path, out_path))

	if not sequence_tasks:
		print("No depth folders to process")
		return

	total_ok = 0
	with tqdm(sequence_tasks, desc="Converting GT depth", unit="seq") as pbar:
		for depth_path, out_path in pbar:
			pbar.set_description(f"Converting {os.path.basename(out_path)}")
			depth = load_depth(depth_path)
			if depth is None:
				continue
			vis = depth_to_vis_png(depth)
			if cv2.imwrite(out_path, vis):
				total_ok += 1

	print("✓ Depth visualization conversion completed")
	print(f"  Sequences: {len(sequence_tasks)}")
	print(f"  Files:     {total_ok}/{len(sequence_tasks)}")


def main():
	# ==================== Config ====================
	BASE_DIR = "/home_ssd/sjy/UE5_Project/PCGBiomeForestPoplar/Saved/MovieRenders/train_data"
	OUTPUT_BASE = "/home_ssd/sjy/deocc_swin/train_data/depth_vis"
	TRAJECTORIES = None  # None -> process all trajectory folders under BASE_DIR
	# ================================================

	batch_convert(BASE_DIR, OUTPUT_BASE, TRAJECTORIES)


if __name__ == "__main__":
	main()

