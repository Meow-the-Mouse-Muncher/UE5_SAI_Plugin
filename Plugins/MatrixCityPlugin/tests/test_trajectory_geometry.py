"""CPU geometry regressions; does not import Unreal or launch rendering."""
import ast
import importlib.util
import math
from pathlib import Path
import random
import sys
import types
import unittest

import numpy as np

PYTHON_DIR = Path(__file__).resolve().parents[1] / 'Content' / 'Python'
spec = importlib.util.spec_from_file_location('trajectory_geometry', PYTHON_DIR / 'trajectory_geometry.py')
geometry = importlib.util.module_from_spec(spec)
spec.loader.exec_module(geometry)


def angle_between(first, second):
    cosine = (np.trace(first.T @ second) - 1.) / 2.
    return math.degrees(math.acos(np.clip(cosine, -1., 1.)))


class TrajectoryGeometryTests(unittest.TestCase):
    def setUp(self):
        self.target = np.array([137., -281., 423.])

    def paths(self, height, yaw):
        return {
            'fix_line': geometry.line(self.target, 25, height, 3500., yaw),
            'plane_grid': geometry.planar(self.target, 25, height, 3500., yaw),
            'rot_arc': geometry.arc(self.target, 25, height, 90., yaw),
            'rot_line': geometry.tracking_line(self.target, 25, height, 90., yaw),
            'rot_spiral': geometry.spiral(self.target, 25, height, 90., 1.5, yaw),
            'rand_shell': geometry.random_shell(self.target, 25, 5000., 12000., 90., height, yaw, random.Random(42)),
        }

    def test_all_configurations_have_identical_exact_gt_pose(self):
        for height in (5000., 6000., 7000., 8000., 10000., 12000.):
            for yaw in (0., 120., 240.):
                expected_position, expected_rotation = geometry.canonical_pose(self.target, height, yaw)
                for kind, (positions, rotations) in self.paths(height, yaw).items():
                    with self.subTest(height=height, yaw=yaw, trajectory=kind):
                        self.assertEqual(positions.shape, (25, 3))
                        self.assertEqual(rotations.shape, (25, 3))
                        self.assertTrue(np.isfinite(positions).all() and np.isfinite(rotations).all())
                        self.assertEqual(len(np.unique(positions.round(7), axis=0)), 25)
                        index = 24 if kind in ('rot_spiral', 'rand_shell') else 12
                        np.testing.assert_array_equal(positions[index], expected_position)
                        np.testing.assert_array_equal(rotations[index], expected_rotation)

    def test_tracking_axes_and_true_pose_continuity(self):
        for yaw in (0., 120., 240., 179.999, -179.999):
            for kind, (positions, rotations) in self.paths(5000., yaw).items():
                matrices = np.array([geometry.rotation_matrix(rot) for rot in rotations])
                changes = np.array([angle_between(a, b) for a, b in zip(matrices[:-1], matrices[1:])])
                with self.subTest(yaw=yaw, trajectory=kind):
                    np.testing.assert_allclose(np.linalg.det(matrices), 1., atol=1e-10)
                    if kind in ('fix_line', 'plane_grid'):
                        self.assertLess(changes.max(), 1e-5)
                    else:
                        self.assertLess(changes.max(), 20.)
                        directions = self.target - positions
                        directions /= np.linalg.norm(directions, axis=1, keepdims=True)
                        np.testing.assert_allclose(matrices[:, :, 0], directions, atol=1e-10)
                    if kind == 'rot_arc':
                        np.testing.assert_allclose(changes, 3.75, atol=1e-9)

    def test_planar_is_complete_square_with_snake_neighbors(self):
        for yaw in (0., 120., 240.):
            points, rotations = geometry.planar(self.target, 25, 5000., 3500., yaw)
            local = geometry._rotate_xy(points - self.target, -yaw)
            np.testing.assert_allclose(np.unique(local[:, 0].round(6)), [-1750, -875, 0, 875, 1750])
            np.testing.assert_allclose(np.unique(local[:, 1].round(6)), [-1750, -875, 0, 875, 1750])
            np.testing.assert_allclose(np.linalg.norm(np.diff(points, axis=0), axis=1), 875.)

    def test_spiral_turns_are_used_and_final_pose_is_on_the_curve(self):
        for height in (5000., 12000.):
            first, first_rot = geometry.spiral(self.target, 25, height, 90., 1.5, 0.)
            second, _ = geometry.spiral(self.target, 25, height, 90., 1., 0.)
            self.assertFalse(np.allclose(first, second))
            relative = first - self.target
            np.testing.assert_allclose(np.linalg.norm(relative, axis=1), height)
            self.assertTrue(np.all(np.diff(relative[:, 2]) > 0.))
            steps = np.linalg.norm(np.diff(first, axis=0), axis=1)
            self.assertLess(steps.max(), .16 * height)
            self.assertLess(steps[-1], np.median(steps[:-1]) * 1.1)
            changes = [angle_between(geometry.rotation_matrix(a), geometry.rotation_matrix(b)) for a, b in zip(first_rot[:-1], first_rot[1:])]
            self.assertLess(max(changes), 12.)

    def test_random_shell_stays_bounded_and_has_no_appended_gt_jump(self):
        for seed in range(300):
            for height in (5000., 8000., 12000.):
                with self.subTest(seed=seed, height=height):
                    points, rotations = geometry.random_shell(self.target, 25, 5000., 12000., 90., height, 120., random.Random(seed))
                    relative = points - self.target
                    radius = np.linalg.norm(relative, axis=1)
                    self.assertGreaterEqual(radius.min(), 5000. - 1e-7)
                    self.assertLessEqual(radius.max(), 12000. + 1e-7)
                    self.assertTrue(np.all(relative[:, 2] / radius >= math.cos(math.pi / 4.) - 1e-10))
                    steps = np.linalg.norm(np.diff(points, axis=0), axis=1)
                    self.assertGreater(steps.min(), 1e-6)
                    self.assertLess(steps[-1], np.median(steps[:-1]) * 1.6)
                    matrices = [geometry.rotation_matrix(value) for value in rotations]
                    self.assertLess(max(angle_between(a, b) for a, b in zip(matrices[:-1], matrices[1:])), 30.)
                    # Check the actual straight position segments serialized into UE,
                    # including the closest point to the excluded inner sphere.
                    start, difference = relative[:-1], np.diff(relative, axis=0)
                    fraction = np.clip(-np.sum(start * difference, axis=1) / np.sum(difference ** 2, axis=1), 0., 1.)
                    closest = start + fraction[:, None] * difference
                    self.assertGreaterEqual(np.linalg.norm(closest, axis=1).min(), 5000. - 1e-7)

    def test_random_path_is_reproducible(self):
        first = geometry.random_shell(self.target, 25, 5000., 12000., 90., 8000., 240., random.Random(17))
        second = geometry.random_shell(self.target, 25, 5000., 12000., 90., 8000., 240., random.Random(17))
        for a, b in zip(first, second):
            np.testing.assert_array_equal(a, b)

    def test_wrappers_preserve_frame_numbers_and_use_new_geometry(self):
        source = (PYTHON_DIR / 'utils_sequencer.py').read_text(encoding='utf-8')
        names = {'_geometry_sequence_keys', 'fix_line', 'plane_grid', 'rot_arc', 'rot_line', 'rot_spiral', 'rand_shell'}
        tree = ast.parse(source)
        body = [node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name in names]
        namespace = dict(np=np, trajectory_geometry=geometry, SequenceKey=lambda **values: values)
        exec(compile(ast.Module(body=body, type_ignores=[]), '<adapters>', 'exec'), namespace)
        actor = types.SimpleNamespace(get_actor_location=lambda: types.SimpleNamespace(x=137., y=-281., z=423.))
        arguments = {
            'fix_line': dict(angle_degrees=120., height_offset=5000., trajectory_size=3500.),
            'plane_grid': dict(angle_degrees=120., height_offset=5000., trajectory_size=3500.),
            'rot_arc': dict(arc_angle_degrees=90., radius=5000., plane_angle_degrees=120.),
            'rot_line': dict(arc_angle_degrees=90., height=5000., plane_angle_degrees=120.),
            'rot_spiral': dict(num_turns=1.5, arc_angle_degrees=90., radius=5000., start_angle=120.),
            'rand_shell': dict(min_radius=5000., max_radius=12000., arc_angle_degrees=90., mid_height=5000., plane_angle_degrees=120.),
        }
        for kind, values in arguments.items():
            with self.subTest(trajectory=kind):
                keys, end = namespace[kind](actor, 25, current_frame=10, **values)
                self.assertEqual(end, 35)
                self.assertEqual([key['frame'] for key in keys], list(range(10, 35)))
                self.assertEqual(keys[24 if kind in ('rot_spiral', 'rand_shell') else 12]['rotation'], (0., -90., 120.))

    def test_camera_sections_enable_quaternion_interpolation(self):
        tree = ast.parse((PYTHON_DIR / 'utils_sequencer.py').read_text(encoding='utf-8'))
        function = next(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == 'add_possessable_camera_to_sequence')
        function.returns = None
        for arg in function.args.args:
            arg.annotation = None
        writes = []
        section = types.SimpleNamespace(set_editor_property=lambda *args: writes.append(args), set_start_frame=lambda *args: None, set_end_frame=lambda *args: None)
        track = types.SimpleNamespace(get_sections=lambda: [section], add_section=lambda: section)
        binding = types.SimpleNamespace(get_id=lambda: 1)
        sequence = types.SimpleNamespace(add_possessable=lambda actor: binding, add_track=lambda cls: track)
        namespace = dict(unreal=types.SimpleNamespace(MovieSceneCameraCutTrack=object, MovieSceneObjectBindingID=lambda: section), add_transforms_to_binding=lambda *args: track)
        exec(compile(ast.Module(body=[function], type_ignores=[]), '<camera binding>', 'exec'), namespace)
        namespace[function.name](sequence, object(), [], 25, 'LINEAR')
        self.assertIn(('use_quaternion_interpolation', True), writes)

    def test_protocol_rejects_invalid_parameters(self):
        for count in (0, 24, 26, 33):
            with self.assertRaises(ValueError):
                geometry.arc(self.target, count, 5000., 90., 0.)
        for span in (0., 180., 200.):
            with self.assertRaises(ValueError):
                geometry.spiral(self.target, 25, 5000., span, 1.5, 0.)
        with self.assertRaises(ValueError):
            geometry.random_shell(self.target, 25, 5000., 12000., 90., 4000., 0.)


if __name__ == '__main__':
    unittest.main(verbosity=2)
