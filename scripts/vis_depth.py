"""
Convert depth maps to visualization PNG images and a scrollable review page.

Sequence traversal and path style are aligned with align_pose_to_center_inv_h5.py.
"""

import html
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


def write_review_html(review_items, html_path):
	"""Write a single large-image review page for all converted sequences."""
	os.makedirs(os.path.dirname(html_path), exist_ok=True)

	nav_links = []
	sections = []
	for idx, item in enumerate(review_items, 1):
		anchor = f"seq-{idx:04d}"
		trajectory_type = html.escape(item["trajectory_type"])
		sequence_name = html.escape(item["sequence_name"])
		depth_file = html.escape(os.path.basename(item["depth_path"]))
		image_rel = html.escape(os.path.relpath(item["out_path"], os.path.dirname(html_path)))

		nav_links.append(
			f'<a href="#{anchor}">{idx:03d}. {trajectory_type}/{sequence_name}</a>'
		)
		sections.append(
			f"""
			<section class="sequence" id="{anchor}">
				<div class="sequence-header">
					<div>
						<div class="sequence-title">{idx:03d}. {sequence_name}</div>
						<div class="sequence-meta">{trajectory_type} / {depth_file}</div>
					</div>
					<a class="open-link" href="{image_rel}" target="_blank">Open PNG</a>
				</div>
				<img src="{image_rel}" alt="{trajectory_type}/{sequence_name} depth visualization" loading="lazy">
			</section>
			"""
		)

	page = f"""<!doctype html>
<html lang="en">
<head>
	<meta charset="utf-8">
	<meta name="viewport" content="width=device-width, initial-scale=1">
	<title>Depth Review</title>
	<style>
		:root {{
			color-scheme: dark;
			--bg: #101214;
			--panel: #171a1d;
			--text: #f0f2f4;
			--muted: #a8b0b8;
			--line: #2a2f35;
			--accent: #64c6ff;
		}}
		* {{
			box-sizing: border-box;
		}}
		body {{
			margin: 0;
			background: var(--bg);
			color: var(--text);
			font-family: Arial, Helvetica, sans-serif;
		}}
		header {{
			position: sticky;
			top: 0;
			z-index: 5;
			padding: 14px 20px;
			border-bottom: 1px solid var(--line);
			background: rgba(16, 18, 20, 0.96);
		}}
		h1 {{
			margin: 0 0 8px;
			font-size: 18px;
			font-weight: 700;
			letter-spacing: 0;
		}}
		.summary {{
			color: var(--muted);
			font-size: 13px;
		}}
		nav {{
			display: flex;
			gap: 8px;
			margin-top: 12px;
			overflow-x: auto;
			padding-bottom: 2px;
		}}
		nav a,
		.open-link {{
			flex: 0 0 auto;
			color: var(--text);
			border: 1px solid var(--line);
			background: var(--panel);
			text-decoration: none;
			border-radius: 6px;
			padding: 7px 10px;
			font-size: 12px;
		}}
		nav a:hover,
		.open-link:hover {{
			border-color: var(--accent);
			color: var(--accent);
		}}
		main {{
			width: 100%;
		}}
		.sequence {{
			padding: 24px 20px 36px;
			border-bottom: 1px solid var(--line);
		}}
		.sequence-header {{
			display: flex;
			align-items: center;
			justify-content: space-between;
			gap: 16px;
			margin-bottom: 12px;
		}}
		.sequence-title {{
			font-size: 18px;
			font-weight: 700;
		}}
		.sequence-meta {{
			margin-top: 4px;
			color: var(--muted);
			font-size: 13px;
		}}
		img {{
			display: block;
			width: 100%;
			height: auto;
			background: #000;
			border: 1px solid var(--line);
		}}
		@media (max-width: 720px) {{
			header,
			.sequence {{
				padding-left: 12px;
				padding-right: 12px;
			}}
			.sequence-header {{
				align-items: flex-start;
				flex-direction: column;
			}}
		}}
	</style>
</head>
<body>
	<header>
		<h1>Depth Review</h1>
		<div class="summary">{len(review_items)} sequences. Each image is displayed at full page width for manual inspection.</div>
		<nav>
			{''.join(nav_links)}
		</nav>
	</header>
	<main>
		{''.join(sections)}
	</main>
</body>
</html>
"""

	with open(html_path, "w", encoding="utf-8") as f:
		f.write(page)


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


def batch_convert(base_dir, output_base, trajectories=None, review_html=True, html_name="review.html"):
	sequence_tasks = []

	if not os.path.exists(base_dir):
		print(f"Base dir {base_dir} does not exist")
		return

	if trajectories is None:
		# 默认处理 base_dir 下所有轨迹子文件夹
		trajectories = sorted([
			d for d in os.listdir(base_dir)
			if os.path.isdir(os.path.join(base_dir, d))
		])

	for trajectory_type in trajectories:
		trajectory_dir = os.path.join(base_dir, trajectory_type)
		if not os.path.isdir(trajectory_dir):
			continue

		trajectory_out_dir = os.path.join(output_base, trajectory_type)
		os.makedirs(trajectory_out_dir, exist_ok=True)

		for sequence_name in sorted(os.listdir(trajectory_dir)):
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
			sequence_tasks.append(
				{
					"trajectory_type": trajectory_type,
					"sequence_name": sequence_name,
					"depth_path": depth_path,
					"out_path": out_path,
				}
			)

	if not sequence_tasks:
		print("No depth folders to process")
		return

	total_ok = 0
	review_items = []
	with tqdm(sequence_tasks, desc="Converting GT depth", unit="seq") as pbar:
		for item in pbar:
			depth_path = item["depth_path"]
			out_path = item["out_path"]
			pbar.set_description(f"Converting {os.path.basename(out_path)}")
			depth = load_depth(depth_path)
			if depth is None:
				continue
			vis = depth_to_vis_png(depth)
			if cv2.imwrite(out_path, vis):
				total_ok += 1
				review_items.append(item)

	print("✓ Depth visualization conversion completed")
	print(f"  Sequences: {len(sequence_tasks)}")
	print(f"  Files:     {total_ok}/{len(sequence_tasks)}")

	if review_html:
		html_path = os.path.join(output_base, html_name)
		write_review_html(review_items, html_path)
		print(f"  Review:    {html_path}")


def main():
	# ==================== Config ====================
	BASE_DIR = "/home_ssd/sjy/UE5_Project/PCGBiomeForestPoplar/Saved/MovieRenders_rot/train_data"
	OUTPUT_BASE = "/home_ssd/sjy/UE5_Project/PCGBiomeForestPoplar/Saved/depth_vis"
	TRAJECTORIES = None  # None -> process all trajectory folders under BASE_DIR
	REVIEW_HTML = True
	HTML_NAME = "review.html"
	# ================================================

	batch_convert(BASE_DIR, OUTPUT_BASE, TRAJECTORIES, REVIEW_HTML, HTML_NAME)


if __name__ == "__main__":
	main()
