"""Path-stable configuration and work planning, with no Unreal dependency."""
import copy
import itertools
import json
from pathlib import Path

import yaml


PLUGIN_ROOT = Path(__file__).resolve().parent.parent


def resolve_path(value, relative_to=None):
    path = Path(value).expanduser()
    if path.is_absolute():
        return path.resolve()
    base = Path(relative_to or PLUGIN_ROOT)
    if (base / path).exists():
        return (base / path).resolve()
    return (PLUGIN_ROOT / path).resolve()


def load_render_config(path, _seen=None):
    path = Path(path).resolve()
    seen = set() if _seen is None else set(_seen)
    if path in seen:
        raise ValueError("Cyclic _BASE_ config: {}".format(path))
    seen.add(path)
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(data, dict):
        raise ValueError("Render YAML must contain an object")
    base = data.pop("_BASE_", None)
    result = load_render_config(path.parent / base, seen) if base else {}

    def merge(source, target):
        for key, value in source.items():
            if isinstance(value, dict) and isinstance(target.get(key), dict):
                merge(value, target[key])
            else:
                target[key] = copy.deepcopy(value)

    merge(data, result)
    return result


def resolve_map(project_file, value):
    content = Path(project_file).resolve().parent / "Content"
    if value.startswith("/Game/"):
        path = content / (value[len("/Game/"):] + ".umap")
        if not path.is_file() or not path.resolve().is_relative_to(content.resolve()):
            raise ValueError("Map asset does not exist: {}".format(value))
        return value
    name = value.removesuffix(".umap")
    if "/" in name or "\\" in name or name in ("", ".", ".."):
        raise ValueError("Use a map name or full /Game/... package path")
    direct = content / "Map" / (name + ".umap")
    if direct.is_file():
        return "/Game/Map/" + name
    matches = list((content / "Map").rglob(name + ".umap"))
    if len(matches) != 1:
        raise ValueError("Map {} has {} matches; specify its full package path".format(name, len(matches)))
    return "/Game/" + matches[0].relative_to(content).with_suffix("").as_posix()


def trajectory_tasks(config):
    params = config["global"]
    keys = list(itertools.product(params["trajectory_types"], params["camera_heights"], params["plane_angles"]))
    if not keys or len(keys) != len(set(keys)):
        raise ValueError("Trajectory product must be nonempty and contain no duplicate tasks")
    # Output names use integer meters/degrees; reject collisions before workers run.
    names = [(kind, int(height / 100), int(angle)) for kind, height, angle in keys]
    if len(names) != len(set(names)):
        raise ValueError("Height/angle parameters collide in canonical output filenames")
    return [list(key) for key in keys]


def partition_tasks(tasks, count):
    if count < 1:
        raise ValueError("shards_per_scene must be positive")
    return [part for part in [tasks[index::count] for index in range(min(count, len(tasks)))] if part]


def dump_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(json.dumps(value, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    temp.replace(path)
