"""Offline storage migration checks; all filesystem writes stay in temp dirs."""
from pathlib import Path
import os
import shutil
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "misc"))
import render_storage as storage


class RenderStorageTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.base = Path(self.temporary.name)
        self.project = self.base / "Forest"
        self.project.mkdir()
        self.project_file = self.project / "Forest.uproject"
        self.project_file.write_text("{}")
        self.runtime = self.base / "runtime"
        self.processes = mock.patch.object(storage, "_active_renderer_pids", return_value=[]).start()
        self.addCleanup(mock.patch.stopall)

    def data(self, name="Saved", value=b"original data"):
        directory = self.project / name
        directory.mkdir(exist_ok=True)
        (directory / "nested").mkdir(exist_ok=True)
        (directory / "nested" / "data.bin").write_bytes(value)
        return directory

    def prepare(self, **kwargs):
        return storage.prepare_project_storage(self.project_file, self.runtime, **kwargs)

    def test_runtime_root_environment_then_config_then_default(self):
        self.assertEqual(storage.resolve_runtime_root(self.project_file, {"runtime_root": str(self.runtime)}, {}), self.runtime)
        override = self.base / "override"
        self.assertEqual(storage.resolve_runtime_root(self.project_file, {"runtime_root": str(self.runtime)},
            {"FOREST_RUNTIME_ROOT": str(override)}), override)
        self.assertEqual(storage.resolve_runtime_root(self.project_file, environ={}),
            Path("/mnt/cpfs_b5/perception_data/sjy/forest_runtime/Forest"))

    def test_missing_and_empty_directories_become_absolute_links(self):
        (self.project / "Saved").mkdir()
        paths = self.prepare()
        for name in storage.PROJECT_DIRECTORIES:
            self.assertEqual(os.readlink(self.project / name), str(self.runtime / name))
            self.assertTrue(paths[name].is_dir())
        self.assertTrue(paths["tmp"].is_dir())
        self.assertTrue(paths["cache"].is_dir())
        self.processes.assert_not_called()

    def test_existing_data_requires_explicit_migration_without_writes(self):
        source = self.data()
        with self.assertRaisesRegex(ValueError, "--migrate"):
            self.prepare()
        self.assertFalse(self.runtime.exists())
        self.assertFalse(source.is_symlink())
        self.assertEqual((source / "nested/data.bin").read_bytes(), b"original data")

    def test_dry_run_inspects_existing_data_without_migration_or_writes(self):
        source = self.data()
        paths = self.prepare(dry_run=True)
        self.assertEqual(paths["Saved"], self.runtime / "Saved")
        self.assertFalse(self.runtime.exists())
        self.assertFalse(source.is_symlink())
        self.processes.assert_not_called()

    def test_migration_preserves_data_modes_and_links_and_leaves_assets(self):
        source = self.data(value=bytes(range(256)) * 10000)
        (source / "nested/data.bin").chmod(0o640)
        (source / "internal").symlink_to("nested", target_is_directory=True)
        external = self.base / "external"
        external.mkdir()
        (external / "untouched").write_text("outside")
        (source / "absolute").symlink_to(external, target_is_directory=True)
        asset = self.project / "Content/Sequences/_workers/trajectory.uasset"
        asset.parent.mkdir(parents=True)
        asset.write_bytes(b"asset")
        paths = self.prepare(migrate=True)
        self.assertEqual((paths["Saved"] / "nested/data.bin").read_bytes(), bytes(range(256)) * 10000)
        self.assertEqual((paths["Saved"] / "nested/data.bin").stat().st_mode & 0o777, 0o640)
        self.assertEqual(os.readlink(paths["Saved"] / "internal"), "nested")
        self.assertEqual(os.readlink(paths["Saved"] / "absolute"), str(external))
        self.assertEqual((external / "untouched").read_text(), "outside")
        self.assertEqual(asset.read_bytes(), b"asset")
        self.assertFalse(list(self.project.glob(".*-migrate-*")))

    def test_idempotent_links_reuse_existing_runtime_data(self):
        self.prepare()
        (self.runtime / "Saved/data").write_text("keep")
        self.prepare(migrate=True)
        self.assertEqual((self.project / "Saved/data").read_text(), "keep")
        self.processes.assert_not_called()

    def test_conflict_in_later_directory_prevents_all_mutations(self):
        self.data("Saved")
        source = self.data("Intermediate")
        target = self.runtime / "Intermediate"
        target.mkdir(parents=True)
        (target / "other").write_text("other data")
        with self.assertRaisesRegex(ValueError, "merge"):
            self.prepare(migrate=True)
        self.assertFalse((self.runtime / "Saved").exists())
        self.assertFalse(source.is_symlink())
        self.assertEqual((target / "other").read_text(), "other data")

    def test_existing_empty_target_is_usable(self):
        self.data()
        (self.runtime / "Saved").mkdir(parents=True)
        self.prepare(migrate=True)
        self.assertEqual((self.runtime / "Saved/nested/data.bin").read_bytes(), b"original data")

    def test_wrong_or_relative_project_link_is_rejected(self):
        for destination in (str(self.base / "other"), "../runtime/Saved"):
            with self.subTest(destination=destination):
                (self.project / "Saved").symlink_to(destination, target_is_directory=True)
                with self.assertRaisesRegex(ValueError, "unexpected symlink"):
                    self.prepare(dry_run=True)
                (self.project / "Saved").unlink()
        self.assertFalse(self.runtime.exists())

    def test_nested_source_and_target_are_rejected(self):
        for runtime in (self.project, self.project / "Saved/runtime"):
            with self.subTest(runtime=runtime):
                with self.assertRaisesRegex(ValueError, "overlap"):
                    storage.prepare_project_storage(self.project_file, runtime, dry_run=True)
        nested = self.base / "Saved/project"
        nested.mkdir(parents=True)
        with self.assertRaisesRegex(ValueError, "overlap"):
            storage.prepare_project_storage(nested, self.base, dry_run=True)

    def test_runtime_target_link_is_rejected_without_following_it(self):
        self.runtime.mkdir()
        external = self.base / "external"
        external.mkdir()
        (self.runtime / "Saved").symlink_to(external, target_is_directory=True)
        with self.assertRaisesRegex(ValueError, "real directory"):
            self.prepare()
        self.assertEqual(list(external.iterdir()), [])

    def test_runtime_root_file_is_rejected_even_in_dry_run(self):
        self.runtime.write_text("keep")
        with self.assertRaisesRegex(ValueError, "Runtime root"):
            self.prepare(dry_run=True)
        self.assertEqual(self.runtime.read_text(), "keep")

    def test_bad_cache_target_is_detected_before_migrating_any_source(self):
        source = self.data()
        self.runtime.mkdir()
        (self.runtime / "cache").write_text("keep")
        with self.assertRaisesRegex(ValueError, "real directory"):
            self.prepare(migrate=True)
        self.assertFalse(source.is_symlink())
        self.assertFalse((self.runtime / "Saved").exists())

    def test_escaping_relative_link_is_rejected_before_any_write(self):
        source = self.data()
        (source / "escape").symlink_to("../../external", target_is_directory=True)
        with self.assertRaisesRegex(ValueError, "Relative symlink"):
            self.prepare(migrate=True)
        self.assertFalse(self.runtime.exists())

    def test_special_file_is_rejected_before_any_write(self):
        source = self.data()
        os.mkfifo(source / "fifo")
        with self.assertRaisesRegex(ValueError, "special file"):
            self.prepare(migrate=True)
        self.assertFalse(self.runtime.exists())

    def test_active_renderer_blocks_migration_before_any_write(self):
        self.data()
        self.processes.return_value = [1234]
        with self.assertRaisesRegex(RuntimeError, "1234"):
            self.prepare(migrate=True)
        self.assertFalse(self.runtime.exists())

    def test_copy_failure_preserves_original(self):
        source = self.data()
        with mock.patch.object(storage.shutil, "copytree", side_effect=OSError("disk full")):
            with self.assertRaisesRegex(OSError, "disk full"):
                self.prepare(migrate=True)
        self.assertFalse(source.is_symlink())
        self.assertEqual((source / "nested/data.bin").read_bytes(), b"original data")
        self.assertFalse((self.runtime / "Saved").exists())
        self.assertEqual(list(self.runtime.iterdir()), [])

    def test_copy_corruption_or_source_changes_prevent_deleting_original(self):
        original_copy = shutil.copytree
        for change_source in (False, True):
            with self.subTest(change_source=change_source):
                source = self.data()

                def copy(*args, **kwargs):
                    result = original_copy(*args, **kwargs)
                    if Path(args[0]) == source:
                        changed = source if change_source else Path(args[1])
                        (changed / "nested/data.bin").write_bytes(b"different data")
                    return result

                with mock.patch.object(storage.shutil, "copytree", side_effect=copy):
                    with self.assertRaisesRegex(RuntimeError, "verification failed"):
                        self.prepare(migrate=True)
                self.assertFalse(source.is_symlink())
                expected = b"different data" if change_source else b"original data"
                self.assertEqual((source / "nested/data.bin").read_bytes(), expected)
                self.assertFalse((self.runtime / "Saved").exists())

    def test_link_failure_restores_original_and_keeps_verified_copy(self):
        source = self.data()
        with mock.patch.object(Path, "symlink_to", side_effect=OSError("no space for link")):
            with self.assertRaisesRegex(OSError, "no space for link"):
                self.prepare(migrate=True)
        self.assertFalse(source.is_symlink())
        self.assertEqual((source / "nested/data.bin").read_bytes(), b"original data")
        self.assertEqual((self.runtime / "Saved/nested/data.bin").read_bytes(), b"original data")
        self.assertFalse(list(self.project.glob(".*-migrate-*")))

    def test_renderer_detection_uses_program_arguments(self):
        match = lambda argv: storage._is_project_renderer(argv, self.project, self.project)
        self.assertTrue(match(["/opt/UnrealEditor", str(self.project_file)]))
        self.assertTrue(match(["python", str(self.project / "Plugins/render_dataset.py"), "--config", "worker.json"]))
        self.assertFalse(match(["pytest", str(self.project / "tests/test_render_storage.py")]))
        self.assertFalse(match(["python", "-c", "print('UnrealEditor {}')".format(self.project_file)]))
        self.assertFalse(match(["/opt/UnrealEditor", str(self.base / "Other/Other.uproject")]))

    def test_renderer_detection_accepts_ue_project_option_and_path_aliases(self):
        alias = self.base / "project-alias"
        alias.symlink_to(self.project, target_is_directory=True)
        for argument in ("-project=" + str(self.project_file), str(alias / "Forest.uproject")):
            with self.subTest(argument=argument):
                self.assertTrue(storage._is_project_renderer(["/opt/UnrealEditor", argument], self.base, self.project))

    @unittest.skipUnless(os.geteuid() == 0, "Changing directory ownership requires root")
    def test_root_created_runtime_inherits_project_owner(self):
        os.chown(self.project, 12345, 12346)
        paths = self.prepare()
        for path in paths.values():
            with self.subTest(path=path):
                self.assertEqual((path.stat().st_uid, path.stat().st_gid), (12345, 12346))

    @unittest.skipUnless(os.geteuid() == 0, "Changing directory ownership requires root")
    def test_new_directories_inherit_existing_runtime_owner(self):
        self.runtime.mkdir()
        os.chown(self.runtime, 12347, 12348)
        paths = self.prepare()
        for path in paths.values():
            with self.subTest(path=path):
                self.assertEqual((path.stat().st_uid, path.stat().st_gid), (12347, 12348))

    @unittest.skipUnless(os.geteuid() == 0, "Changing directory ownership requires root")
    def test_existing_runtime_directories_keep_their_owners(self):
        self.runtime.mkdir()
        os.chown(self.runtime, 12345, 12346)
        existing = [self.runtime / name for name in ("Saved", "tmp", "cache")]
        for path in existing:
            path.mkdir()
            os.chown(path, 12347, 12348)
        self.prepare()
        for path in existing:
            with self.subTest(path=path):
                self.assertEqual((path.stat().st_uid, path.stat().st_gid), (12347, 12348))

    @unittest.skipUnless(os.geteuid() == 0, "Changing directory ownership requires root")
    def test_migrated_data_keeps_source_owners_instead_of_runtime_owner(self):
        source = self.data()
        os.chown(source, 12345, 12346)
        os.chown(source / "nested/data.bin", 12347, 12348)
        self.runtime.mkdir()
        os.chown(self.runtime, 12349, 12350)
        paths = self.prepare(migrate=True)
        self.assertEqual((paths["Saved"].stat().st_uid, paths["Saved"].stat().st_gid), (12345, 12346))
        migrated = paths["Saved"] / "nested/data.bin"
        self.assertEqual((migrated.stat().st_uid, migrated.stat().st_gid), (12347, 12348))
        self.assertEqual((paths["tmp"].stat().st_uid, paths["tmp"].stat().st_gid), (12349, 12350))


if __name__ == "__main__":
    unittest.main()
