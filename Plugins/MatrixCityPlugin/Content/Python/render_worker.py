"""Validated, Unreal-independent helpers for optional render-worker isolation.

With no worker configuration every selector preserves the legacy job set.
"""
import hashlib
import json
import math
import re
from pathlib import Path


_worker_config = {}
PERFORMANCE_DEFAULTS = {
    "queue_wait_cycles": 3,
    "queue_wait_seconds": 0.5,
    "queue_collect_garbage": True,
    "step_wait_seconds": 1.0,
    "step_collect_garbage": True,
    "map_wait_cycles": 3,
    "map_wait_seconds": 1.0,
    "exit_wait_cycles": 2,
    "exit_wait_seconds": 1.5,
}


def configure_worker(config_path=None):
    global _worker_config
    if not config_path:
        _worker_config = {}
        return _worker_config
    source = Path(config_path).expanduser().resolve()
    with source.open(encoding="utf-8") as stream:
        config = json.load(stream)
    if not isinstance(config, dict):
        raise ValueError("worker_config_path must contain a JSON object")
    validate_config(config)
    # Resolve worker-owned paths relative to the worker JSON, never the UE cwd.
    for key in ("trajectory_config_path", "telemetry_path", "export_output_path"):
        if config.get(key):
            value = Path(config[key]).expanduser()
            config[key] = str((source.parent / value).resolve() if not value.is_absolute() else value)
    _worker_config = config
    return config


def get_worker_config():
    return _worker_config


def _integer(value, name, minimum=0):
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        raise ValueError("{} must be an integer >= {}".format(name, minimum))
    return value


def task_key(task):
    if not isinstance(task, (tuple, list)) or len(task) != 3:
        raise ValueError("Each trajectory task must be [type, height_cm, angle_degrees]")
    kind, height, angle = task
    if not isinstance(kind, str) or not kind:
        raise ValueError("Trajectory type must be a nonempty string")
    for value in (height, angle):
        if isinstance(value, bool) or not isinstance(value, (float, int)) or not math.isfinite(value):
            raise ValueError("Trajectory height/angle must be finite numbers")
    return kind, float(height), float(angle)


def validate_config(config):
    count = _integer(config.get("target_shard_count", 1), "target_shard_count", 1)
    index = _integer(config.get("target_shard_index", 0), "target_shard_index")
    if index >= count:
        raise ValueError("target_shard_index must be below target_shard_count")
    if "target_names" in config:
        names = config["target_names"]
        if not isinstance(names, list) or any(not isinstance(name, str) or not name for name in names):
            raise ValueError("target_names must be a list of nonempty labels")
        if len(set(names)) != len(names):
            raise ValueError("target_names contains duplicate labels")
    if "trajectory_tasks" in config:
        tasks = config["trajectory_tasks"]
        if not isinstance(tasks, list):
            raise ValueError("trajectory_tasks must be a list")
        keys = [task_key(task) for task in tasks]
        if len(keys) != len(set(keys)):
            raise ValueError("trajectory_tasks contains duplicate tasks")
    if "sequence_root" in config:
        root = config["sequence_root"]
        if (not isinstance(root, str) or not re.fullmatch(r"/Game/Sequences(?:/[A-Za-z0-9_-]+)*", root)):
            raise ValueError("sequence_root must be a safe package path below /Game/Sequences")
    if config.get("map_package_path"):
        path = config["map_package_path"]
        if not isinstance(path, str) or not path.startswith("/Game/") or ".." in path.split("/"):
            raise ValueError("map_package_path must be a full /Game/... package path")
    if "random_seed" in config:
        _integer(config["random_seed"], "random_seed")
    performance_settings({}, config)


def select_target_indices(labels, config=None):
    """Return indices into ALL scene actors; visibility still uses the full list."""
    config = get_worker_config() if config is None else config
    names = config.get("target_names")
    if names is not None:
        missing = set(names) - set(labels)
        if missing:
            raise ValueError("Requested target labels not present: {}".format(sorted(missing)))
    selected = [index for index, label in enumerate(labels) if names is None or label in names]
    shard = config.get("target_shard_index", 0)
    count = config.get("target_shard_count", 1)
    return selected[shard::count]


def selected_trajectory_keys(all_tasks, config=None):
    """Validate exact tasks against the full YAML product without changing its bounds."""
    config = get_worker_config() if config is None else config
    available = [task_key(task) for task in all_tasks]
    if "trajectory_tasks" not in config:
        return set(available)
    selected = {task_key(task) for task in config["trajectory_tasks"]}
    missing = selected - set(available)
    if missing:
        raise ValueError("Requested trajectory tasks absent from trajectory YAML: {}".format(sorted(missing)))
    return selected


def performance_settings(render_config=None, worker_config=None):
    worker_config = get_worker_config() if worker_config is None else worker_config
    settings = dict(PERFORMANCE_DEFAULTS)
    for overrides in ((render_config or {}).get("Performance_Settings", {}), worker_config.get("performance_settings", {})):
        if not isinstance(overrides, dict):
            raise ValueError("Performance_Settings/performance_settings must be an object")
        unknown = set(overrides) - set(PERFORMANCE_DEFAULTS)
        if unknown:
            raise ValueError("Unknown performance settings: {}".format(sorted(unknown)))
        settings.update(overrides)
    for key, value in settings.items():
        if key.endswith("_cycles"):
            _integer(value, key)
        elif key.endswith("_seconds"):
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value < 0:
                raise ValueError("{} must be a finite nonnegative number".format(key))
        elif not isinstance(value, bool):
            raise ValueError("{} must be true/false".format(key))
    return settings


def trajectory_seed(base_seed, map_name, target_name, key):
    """Stable across worker counts and interpreter hash randomization."""
    payload = json.dumps([base_seed, map_name, target_name, task_key(key)], separators=(",", ":"))
    return int.from_bytes(hashlib.sha256(payload.encode("utf-8")).digest()[:8], "big")


def append_telemetry(event, path=None):
    if not path:
        return
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    with destination.open("a", encoding="utf-8") as stream:
        stream.write(json.dumps(event, sort_keys=True) + "\n")
