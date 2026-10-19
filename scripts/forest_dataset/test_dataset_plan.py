"""Planning and frame completeness checks, without Unreal or a GPU."""
import csv
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'Plugins/MatrixCityPlugin/misc'))
import dataset_plan as plan


class DatasetPlanTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.addCleanup(self.temp.cleanup)
        self.package = '/Game/Map/dataset/train/scene_007_grass'
        self.manifest = {'maps': [{'map': self.package, 'source_scene': 'scene_007', 'split': 'train',
            'targets': {'Target_001': {}, 'Target_002': {}}, 'occlusion_actor_names': ['TreeA', 'TreeB']}]}
        self.rows = [{'sequence_id': 'seq_a', 'map_path': self.package, 'source_scene': 'scene_007',
            'split': 'train', 'target_label': 'Target_001', 'expected_views': '25',
            'trajectory': 'fix_line', 'height_m': '80', 'yaw_deg': '120'},
            {'sequence_id': 'seq_b', 'map_path': self.package, 'source_scene': 'scene_007',
            'split': 'train', 'target_label': 'Target_002', 'expected_views': '25',
            'trajectory': 'rot_arc', 'height_m': '60', 'yaw_deg': '0'}]
        self.allowed = [['fix_line', 8000, 120], ['rot_arc', 6000, 0]]

    def jobs(self, rows=None, targets=None):
        path = self.root / 'plan.csv'
        with path.open('w', newline='', encoding='utf-8') as stream:
            writer = csv.DictWriter(stream, list(self.rows[0]));writer.writeheader()
            writer.writerows(self.rows if rows is None else rows)
        return plan.jobs_from_csv([path], self.manifest, [self.package], targets, 1, None, self.allowed)

    def test_sparse_target_recipes_are_not_cartesian_expanded(self):
        jobs, summary, rows = self.jobs()
        self.assertEqual(summary['selected_sequences'], 2)
        self.assertEqual([j['trajectory_tasks'] for j in jobs], [[self.allowed[0]], [self.allowed[1]]])
        self.assertEqual(jobs[0]['occlusion_actor_names'], ['TreeA', 'TreeB'])

    def test_filter_preserves_complete_target_label(self):
        jobs, summary, rows = self.jobs(targets=['Target_002'])
        self.assertEqual(summary['selected_sequences'], 1)
        self.assertEqual(jobs[0]['target_names'], ['Target_002'])

    def test_wrong_source_split_rejected(self):
        bad = [dict(self.rows[0], split='test')]
        with self.assertRaises(ValueError):self.jobs(bad)

    def test_duplicate_sequence_rejected(self):
        with self.assertRaises(ValueError):self.jobs([self.rows[0], self.rows[0]])

    def test_unknown_target_rejected(self):
        with self.assertRaises(ValueError):self.jobs(targets=['Target_999'])

    def test_partial_frame_set_never_counts_as_success(self):
        jobs, _, _ = self.jobs(targets=['Target_001'])
        render = {'Render_Passes': [{'pass_name': 'rgb', 'enable': True, 'ext': 'png'},
                                    {'pass_name': 'depth', 'enable': True, 'ext': 'exr'}]}
        for suffix in ('occ', 'GT'):
            for kind, ext in (('rgb', 'png'), ('depth', 'exr')):
                folder = self.root / 'fix_line' / ('scene_007_grass_Target_001_height_080_ang_120_' + suffix) / kind
                folder.mkdir(parents=True)
                for index in range(25):(folder / ('%04d.' % index + ext)).write_bytes(b'nonempty')
        plan.check_output(self.root, jobs[0], render)
        next(self.root.rglob('depth/0024.exr')).unlink()
        with self.assertRaises(ValueError):plan.check_output(self.root, jobs[0], render)


if __name__ == '__main__':unittest.main()
