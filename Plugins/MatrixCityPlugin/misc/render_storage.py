"""Keep disposable Unreal project state on a separate runtime filesystem.

Normal launches only create links for missing/empty directories. Moving existing
data is an explicit, offline operation: ``render_storage.py --project ... --migrate``.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import stat
import tempfile
import uuid


PROJECT_DIRECTORIES = ("Saved", "Intermediate", "DerivedDataCache")
RENDER_PROGRAMS = {
    "UnrealEditor", "UnrealEditor-Cmd", "run-editor-fixed", "render_dataset.py",
    "render_forest_dataset.py", "render_forest_dataset.sh", "run_cmd_async.py",
    "run_parallel.py", "benchmark_render.py",
}


def _project_directory(project):
    project = Path(project).expanduser().resolve()
    directory = project.parent if project.suffix == ".uproject" else project
    if not directory.is_dir():
        raise ValueError("Project directory does not exist: {}".format(directory))
    return directory


def default_runtime_root(project):
    return Path("/mnt/cpfs_b5/perception_data/sjy/forest_runtime") / _project_directory(project).name


def resolve_runtime_root(project, config=None, environ=None):
    environ = os.environ if environ is None else environ
    value = environ.get("FOREST_RUNTIME_ROOT") or (config or {}).get("runtime_root")
    return Path(value).expanduser().resolve() if value else default_runtime_root(project)


def _is_project_renderer(argv, cwd, project):
    """Match actual program/path arguments, not text embedded in pytest or -c."""
    if not any(Path(argument).name in RENDER_PROGRAMS for argument in argv):
        return False
    for argument in argv:
        if argument.startswith("-"):
            option, separator, value = argument.partition("=")
            if option not in ("-project", "--project", "-config", "--config") or not separator:
                continue
            argument = value.strip('"')
        if "\n" in argument:
            continue
        path = Path(argument)
        if not path.is_absolute():
            path = cwd / path
        # abspath preserves the project spelling even when Saved is a symlink.
        path = Path(os.path.abspath(path))
        if path.is_relative_to(project) or path.resolve().is_relative_to(project):
            return True
    return False


def _active_renderer_pids(project):
    pids = []
    for process in Path("/proc").iterdir():
        if not process.name.isdigit() or int(process.name) == os.getpid():
            continue
        try:
            argv = [os.fsdecode(part) for part in (process / "cmdline").read_bytes().split(b"\0") if part]
            cwd = (process / "cwd").resolve(strict=True)
        except (FileNotFoundError, PermissionError, ProcessLookupError):
            continue
        if _is_project_renderer(argv, cwd, project):
            pids.append(int(process.name))
    return sorted(pids)


def _manifest(root, hash_files=True):
    """Inventory without following directory links; reject unsupported entries."""
    result = {}

    def visit(path):
        info = path.lstat()
        relative = path.relative_to(root).as_posix()
        mode = stat.S_IMODE(info.st_mode)
        if stat.S_ISLNK(info.st_mode):
            link = os.readlink(path)
            if not os.path.isabs(link):
                destination = Path(os.path.abspath(path.parent / link))
                if not destination.is_relative_to(root):
                    raise ValueError("Relative symlink leaves migrated directory: {} -> {}".format(path, link))
            result[relative] = ("link", link)
        elif stat.S_ISDIR(info.st_mode):
            result[relative] = ("directory", mode)
            for child in sorted(path.iterdir()):
                visit(child)
        elif stat.S_ISREG(info.st_mode):
            digest = None
            if hash_files:
                checksum = hashlib.sha256()
                with path.open("rb") as stream:
                    for block in iter(lambda: stream.read(1024 * 1024), b""):
                        checksum.update(block)
                digest = checksum.hexdigest()
            result[relative] = ("file", info.st_size, mode, digest)
        else:
            raise ValueError("Cannot migrate special file: {}".format(path))

    visit(root)
    return result


def _preserve_ownership(source, destination):
    if os.geteuid() != 0:
        return
    info = source.lstat()
    os.chown(destination, info.st_uid, info.st_gid, follow_symlinks=False)
    if source.is_dir() and not source.is_symlink():
        for child in source.iterdir():
            _preserve_ownership(child, destination / child.name)


def _mkdir_owned(path, owner):
    """Set ownership only when this call creates the directory."""
    try:
        path.mkdir(parents=True)
    except FileExistsError:
        if not path.is_dir():
            raise
    else:
        if os.geteuid() == 0:
            os.chown(path, *owner)


def _migrate_directory(source, target):
    before = _manifest(source)
    staging = Path(tempfile.mkdtemp(prefix=".{}-migrate-".format(target.name), dir=target.parent))
    try:
        shutil.copytree(source, staging, symlinks=True, dirs_exist_ok=True)
        _preserve_ownership(source, staging)
        if _manifest(staging) != before or _manifest(source) != before:
            raise RuntimeError("Migration verification failed; original retained: {}".format(source))
        # rename refuses a target directory that became nonempty after preflight.
        staging.replace(target)
    finally:
        if staging.exists():
            shutil.rmtree(staging)

    backup = source.with_name(".{}-migrate-{}".format(source.name, uuid.uuid4().hex))
    source.rename(backup)
    try:
        source.symlink_to(target, target_is_directory=True)
    except BaseException:
        backup.rename(source)
        raise
    # The verified copy and usable project link exist before removing originals.
    shutil.rmtree(backup)


def prepare_project_storage(project, runtime_root=None, *, migrate=False, dry_run=False):
    """Return runtime paths; explicitly migrate data only when ``migrate=True``.

    ``dry_run`` checks the complete plan without creating or changing files and
    allows inspecting a migration without opting into it. Existing correct links
    are reusable; two nonempty trees are never merged. Copies are SHA256-checked
    against both the source and its initial snapshot before originals are removed.
    """
    project = _project_directory(project)
    runtime_root = resolve_runtime_root(project) if runtime_root is None else Path(runtime_root).expanduser().resolve()
    if runtime_root.exists() and not runtime_root.is_dir():
        raise ValueError("Runtime root must be a directory: {}".format(runtime_root))
    paths = {"runtime_root": runtime_root}
    paths.update({name: runtime_root / name for name in (*PROJECT_DIRECTORIES, "tmp", "cache")})
    sources = [project / name for name in PROJECT_DIRECTORIES]
    targets = [paths[name] for name in PROJECT_DIRECTORIES]
    if any(source.is_relative_to(target) or target.is_relative_to(source)
           for source in sources for target in targets):
        raise ValueError("Project state and runtime directories must not overlap")

    migrations = []
    for source, target in zip(sources, targets):
        if target.is_symlink() or (target.exists() and not target.is_dir()):
            raise ValueError("Runtime target must be a real directory: {}".format(target))
        if source.is_symlink():
            if os.readlink(source) != str(target):
                raise ValueError("Project directory has an unexpected symlink: {}".format(source))
            continue
        if source.exists() and not source.is_dir():
            raise ValueError("Project state must be a directory: {}".format(source))
        if source.exists() and any(source.iterdir()):
            if target.exists() and any(target.iterdir()):
                raise ValueError("Refusing to merge nonempty directories: {} and {}".format(source, target))
            if not migrate and not dry_run:
                raise ValueError("Existing project data requires an offline --migrate first: {}".format(source))
            _manifest(source, hash_files=False)
            migrations.append((source, target))
    for name in ("tmp", "cache"):
        if paths[name].is_symlink() or (paths[name].exists() and not paths[name].is_dir()):
            raise ValueError("Runtime target must be a real directory: {}".format(paths[name]))
    if dry_run:
        return paths
    if migrations:
        pids = _active_renderer_pids(project)
        if pids:
            raise RuntimeError("Stop project renderers before migration; active PIDs: {}".format(pids))

    project_info = project.stat()
    _mkdir_owned(runtime_root, (project_info.st_uid, project_info.st_gid))
    runtime_info = runtime_root.stat()
    owner = (runtime_info.st_uid, runtime_info.st_gid)
    for source, target in zip(sources, targets):
        if (source, target) in migrations:
            _migrate_directory(source, target)
        else:
            _mkdir_owned(target, owner)
            if not source.is_symlink():
                if source.exists():
                    source.rmdir()
                source.symlink_to(target, target_is_directory=True)
    for name in ("tmp", "cache"):
        _mkdir_owned(paths[name], owner)
    return paths


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project", required=True)
    parser.add_argument("--runtime-root")
    parser.add_argument("--migrate", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)
    paths = prepare_project_storage(args.project, args.runtime_root, migrate=args.migrate, dry_run=args.dry_run)
    print(json.dumps({name: str(path) for name, path in paths.items()}, indent=2))


if __name__ == "__main__":
    main()
