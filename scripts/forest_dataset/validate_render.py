"""Decode the actual planned RGB/depth frames and verify exported sequences."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import sys

os.environ['OPENCV_IO_ENABLE_OPENEXR'] = '1'
import cv2
import numpy as np
from PIL import Image

PROJECT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT / 'Plugins/MatrixCityPlugin/misc'))
import dataset_plan


def run(root):
    root = Path(root).resolve()
    run_dir = root if (root / 'plan.json').is_file() else root / 'run'
    plan = json.loads((run_dir / 'plan.json').read_text())
    result = json.loads((run_dir / 'result.json').read_text())
    if not result['success']:
        raise RuntimeError('The render scheduler did not finish successfully')
    output = Path(plan['output_root'])
    config = plan['render_config']
    width, height = config['Resolution']
    report = {'success': False, 'scope': 'Actual planned RGB/depth/FBX files; no claim of full production capture.',
              'run_dir': str(run_dir), 'output_root': str(output), 'maps': {}, 'jobs': len(plan['jobs']),
              'rgb_files': 0, 'depth_files': 0, 'fbx_files': 0, 'errors': []}
    sequences = 0
    for job in plan['jobs']:
        dataset_plan.check_output(output, job, config)
        package = job['map_package_path']
        item = report['maps'].setdefault(package, {'sequences': 0, 'rgb_files': 0, 'depth_files': 0})
        for kind, h, angle in job['trajectory_tasks']:
            for target in job['target_names']:
                name = '{}_{}_height_{:03d}_ang_{:03d}'.format(package.rsplit('/', 1)[-1], target, int(h / 100), int(angle))
                depths = {}
                for suffix in ('occ', 'GT'):
                    folder = output / kind / (name + '_' + suffix)
                    for file in sorted((folder / 'rgb').glob('*.png')):
                        with Image.open(file) as image:
                            if image.size != (width, height):
                                raise ValueError('RGB dimensions: ' + str(file))
                            pixels = np.asarray(image.convert('RGB'))
                        if not np.any(pixels) or pixels.max() == pixels.min():
                            raise ValueError('Empty/constant RGB: ' + str(file))
                        item['rgb_files'] += 1; report['rgb_files'] += 1
                    low, high, medians = [], [], []
                    for file in sorted((folder / 'depth').glob('*.exr')):
                        pixels = cv2.imread(str(file), cv2.IMREAD_UNCHANGED)
                        if pixels is None or pixels.shape[:2] != (height, width) or not np.isfinite(pixels).all():
                            raise ValueError('Invalid EXR: ' + str(file))
                        channel = pixels[..., 0] if pixels.ndim == 3 else pixels
                        positive = channel[channel > 0]
                        if not positive.size:
                            raise ValueError('All-zero depth: ' + str(file))
                        low.append(float(positive.min())); high.append(float(positive.max()))
                        medians.append(float(np.median(positive)))
                        item['depth_files'] += 1; report['depth_files'] += 1
                    depths[suffix] = {'minimum_positive': min(low), 'maximum_positive': max(high),
                                      'median_of_frame_medians': float(np.median(medians)),
                                      'decoded_dtype': str(pixels.dtype)}
                item.setdefault('depth_pairs', []).append({'trajectory': kind, 'target': target,
                    'height_m': h / 100, 'yaw_deg': angle, 'statistics': depths})
                item['sequences'] += 1; sequences += 1
    fbx = list((output / 'Exported_FBX').rglob('*.fbx'))
    if len(fbx) != sequences or any(f.stat().st_size < 1024 for f in fbx):
        raise ValueError('Incomplete FBX exports: %d/%d' % (len(fbx), sequences))
    report.update(success=True, fbx_files=len(fbx), sequences=sequences, map_count=len(report['maps']),
                  all_rgb_decoded=True, all_depth_decoded=True, all_depth_finite_and_nonzero=True)
    target = root / 'render_validation.json'
    target.write_text(json.dumps(report, indent=2), encoding='utf-8')
    print(json.dumps({k: report[k] for k in ('success', 'map_count', 'sequences', 'rgb_files', 'depth_files', 'fbx_files')}))
    return report


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('run_root')
    run(parser.parse_args().run_root)
