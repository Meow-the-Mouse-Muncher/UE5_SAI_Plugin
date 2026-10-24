"""Deployment identity and environment checks, without Unreal or a GPU."""
from pathlib import Path
from types import SimpleNamespace
import os
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'Plugins/MatrixCityPlugin/misc'))
import render_dataset as renderer


class RuntimeTests(unittest.TestCase):
    def test_main_passes_explicit_arguments_before_resolving_render_paths(self):
        arguments = ['--config', 'deployment.json', '--gpu-ids', '0', '--dry-run']
        config_path = mock.Mock()
        config_path.read_text.return_value = '{"run_as_user": "ue"}'
        with mock.patch.object(renderer, 'resolve_path', return_value=config_path) as resolve, \
                mock.patch.object(renderer, 'configure_runtime', side_effect=RuntimeError('runtime reached')) as configure, \
                mock.patch.object(renderer, 'choose_gpus') as choose:
            with self.assertRaisesRegex(RuntimeError, 'runtime reached'):
                renderer.main(arguments)
            configure.assert_called_once_with({'run_as_user': 'ue'}, arguments,
                                              config_dir=config_path.parent)
            resolve.assert_called_once_with('deployment.json')
            choose.assert_not_called()

    def test_default_configuration_preserves_environment(self):
        with mock.patch.dict(os.environ, {'EXISTING': 'keep'}, clear=True):
            renderer.configure_runtime({}, [])
            self.assertEqual(dict(os.environ), {'EXISTING': 'keep'})

    def test_root_reexec_preserves_arguments_before_environment_changes(self):
        arguments = ['--config', 'config with spaces.json', '--targets', 'Target_001', '--dry-run']
        fake_pwd = SimpleNamespace(getpwnam=lambda name: SimpleNamespace(pw_uid=1001))
        with mock.patch.object(sys, 'platform', 'linux'), \
                mock.patch.dict(sys.modules, {'pwd': fake_pwd}), \
                mock.patch.object(os, 'geteuid', return_value=0, create=True), \
                mock.patch.object(os, 'execvp') as execute, \
                mock.patch.dict(os.environ, {'HOME': '/root'}, clear=True):
            renderer.configure_runtime({'run_as_user': 'ue', 'environment': {'HOME': '/opt/ue-home'}}, arguments)
            execute.assert_called_once_with('runuser', ['runuser', '-u', 'ue', '--', sys.executable,
                str(Path(renderer.__file__).resolve()), *arguments])
            self.assertEqual(os.environ['HOME'], '/root')

    def test_target_user_applies_cleanup_then_explicit_environment(self):
        fake_pwd = SimpleNamespace(getpwnam=lambda name: SimpleNamespace(pw_uid=1001))
        with mock.patch.object(sys, 'platform', 'linux'), \
                mock.patch.dict(sys.modules, {'pwd': fake_pwd}), \
                mock.patch.object(os, 'geteuid', return_value=1001, create=True), \
                mock.patch.object(os, 'execvp') as execute, \
                mock.patch.dict(os.environ, {'HOME': '/root', 'DISPLAY': ':0'}, clear=True):
            renderer.configure_runtime({'run_as_user': 'ue', 'unset_environment': ['DISPLAY', 'HOME'],
                                        'environment': {'HOME': '/opt/ue-home'}}, [])
            execute.assert_not_called()
            self.assertEqual(dict(os.environ), {'HOME': '/opt/ue-home'})

    def test_other_unprivileged_user_is_rejected(self):
        fake_pwd = SimpleNamespace(getpwnam=lambda name: SimpleNamespace(pw_uid=1001))
        with mock.patch.object(sys, 'platform', 'linux'), \
                mock.patch.dict(sys.modules, {'pwd': fake_pwd}), \
                mock.patch.object(os, 'geteuid', return_value=1002, create=True), \
                mock.patch.object(os, 'execvp') as execute:
            with self.assertRaisesRegex(PermissionError, 'root or as ue'):
                renderer.configure_runtime({'run_as_user': 'ue'}, [])
            execute.assert_not_called()

    def test_invalid_environment_types_are_rejected(self):
        for config in ({'environment': {'HOME': 1}}, {'environment': []},
                       {'unset_environment': 'DISPLAY'}, {'unset_environment': [1]}):
            with self.subTest(config=config), self.assertRaises(ValueError):
                renderer.configure_runtime(config, [])

    def test_external_runtime_routes_generated_files_and_temporary_data(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            project = root / 'project' / 'forest.uproject'
            project.parent.mkdir()
            project.write_text('{}')
            trajectories = project.parent / 'Content/Sequences/_workers'
            trajectories.mkdir(parents=True)
            (trajectories / 'keep.uasset').write_bytes(b'trajectory')
            runtime = root / 'external'
            with mock.patch.dict(os.environ, {}, clear=True), \
                    mock.patch.object(tempfile, 'tempdir', None), \
                    mock.patch.object(sys, 'dont_write_bytecode', False):
                renderer.configure_runtime({'ue_project': str(project), 'runtime_root': str(runtime)}, [])
                for name in ('Saved', 'Intermediate', 'DerivedDataCache'):
                    self.assertTrue((project.parent / name).is_symlink())
                    self.assertEqual((project.parent / name).resolve(), runtime / name)
                self.assertEqual(tempfile.gettempdir(), str(runtime / 'tmp'))
                self.assertEqual(os.environ['TMPDIR'], str(runtime / 'tmp'))
                self.assertEqual(os.environ['UE_LocalDataCachePath'], str(runtime / 'DerivedDataCache/Local'))
                self.assertEqual(os.environ['XDG_CACHE_HOME'], str(runtime / 'cache'))
                self.assertTrue(sys.dont_write_bytecode)
            self.assertFalse(trajectories.is_symlink())
            self.assertEqual((trajectories / 'keep.uasset').read_bytes(), b'trajectory')

    def test_external_runtime_dry_run_does_not_move_or_create_directories(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            project = root / 'project' / 'forest.uproject'
            saved = project.parent / 'Saved'
            saved.mkdir(parents=True)
            project.write_text('{}')
            checkpoint = saved / 'plan.json'
            checkpoint.write_text('{"checkpoint": true}')
            runtime = root / 'external'
            with mock.patch.dict(os.environ, {}, clear=True), \
                    mock.patch.object(sys, 'dont_write_bytecode', False):
                renderer.configure_runtime({'ue_project': str(project), 'runtime_root': str(runtime)},
                                           ['--dry-run'])
            self.assertFalse(runtime.exists())
            self.assertFalse(saved.is_symlink())
            self.assertEqual(checkpoint.read_text(), '{"checkpoint": true}')


if __name__ == '__main__':
    unittest.main()
