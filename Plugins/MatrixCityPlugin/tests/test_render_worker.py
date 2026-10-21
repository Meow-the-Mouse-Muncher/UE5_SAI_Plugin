import importlib.util
import ast
import json
import random
import types
from unittest import mock
from pathlib import Path
import tempfile
import unittest


PLUGIN_ROOT = Path(__file__).resolve().parents[1]
PYTHON_ROOT = PLUGIN_ROOT / "Content" / "Python"
if not PYTHON_ROOT.is_dir():
    PYTHON_ROOT = PLUGIN_ROOT / "python"
MODULE_PATH = PYTHON_ROOT / "render_worker.py"
spec = importlib.util.spec_from_file_location("render_worker", MODULE_PATH)
worker = importlib.util.module_from_spec(spec)
spec.loader.exec_module(worker)


class WorkerSelectionTests(unittest.TestCase):
    def tearDown(self):
        worker.configure_worker()

    def test_target_shards_cover_all_targets_once(self):
        labels = ["Target_{:03d}".format(number) for number in range(7)]
        shards = [worker.select_target_indices(labels, {
            "target_shard_index": index, "target_shard_count": 3,
        }) for index in range(3)]
        self.assertEqual(sorted(sum(shards, [])), list(range(7)))
        self.assertFalse(set(shards[0]) & set(shards[1]))
        self.assertEqual(worker.select_target_indices(labels, {}), list(range(7)))

    def test_explicit_target_filter_preserves_full_actor_indices(self):
        labels = ["Target_000", "Target_001", "Target_002", "Target_003"]
        self.assertEqual(worker.select_target_indices(labels, {
            "target_names": ["Target_001", "Target_003"],
            "target_shard_count": 2, "target_shard_index": 1,
        }), [3])
        with self.assertRaises(ValueError):
            worker.select_target_indices(labels, {"target_names": ["Target_missing"]})

    def test_exact_trajectory_filter_does_not_change_yaml_domain(self):
        tasks = [(kind, height, angle) for kind in ["rand_shell", "rot_arc"]
                 for height in [5000, 12000] for angle in [0, 120]]
        original = list(tasks)
        chosen = worker.selected_trajectory_keys(tasks, {
            "trajectory_tasks": [["rand_shell", 12000.0, 120.0]],
        })
        self.assertEqual(chosen, {("rand_shell", 12000.0, 120.0)})
        self.assertEqual(tasks, original)
        self.assertEqual(len(worker.selected_trajectory_keys(tasks, {})), 8)
        with self.assertRaises(ValueError):
            worker.selected_trajectory_keys(tasks, {"trajectory_tasks": [["rand_shell", 6000, 0]]})

    def test_validation_prevents_unsafe_assets_and_bad_shards(self):
        for config in ({"sequence_root": "/Game/Map"},
                       {"sequence_root": "/Game/Sequences/../Map"},
                       {"target_shard_count": 0}, {"target_shard_index": 2, "target_shard_count": 2},
                       {"trajectory_tasks": [["rot_arc", 5000, 0], ["rot_arc", 5000.0, 0.0]]},
                       {"performance_settings": {"queue_wait_cycles": -1}},
                       {"performance_settings": {"step_wait_seconds": float("nan")}}):
            with self.subTest(config=config), self.assertRaises(ValueError):
                worker.validate_config(config)

    def test_default_safety_and_opt_in_overrides(self):
        default = worker.performance_settings({}, {})
        self.assertEqual(default["queue_wait_cycles"] * default["queue_wait_seconds"] +
                         default["step_wait_seconds"], 2.5)
        self.assertTrue(default["queue_collect_garbage"])
        self.assertTrue(default["step_collect_garbage"])
        chosen = worker.performance_settings({"Performance_Settings": {"step_wait_seconds": 0.2}},
                                            {"performance_settings": {"step_wait_seconds": 0.1}})
        self.assertEqual(chosen["step_wait_seconds"], 0.1)

    def test_config_paths_and_telemetry(self):
        with tempfile.TemporaryDirectory() as folder:
            source = Path(folder) / "worker.json"
            source.write_text(json.dumps({"trajectory_config_path": "trajectory.yaml",
                                          "telemetry_path": "logs/metrics.jsonl"}), encoding="utf-8")
            config = worker.configure_worker(source)
            self.assertEqual(config["trajectory_config_path"], str(Path(folder) / "trajectory.yaml"))
            worker.append_telemetry({"event": "render_step", "success": True}, config["telemetry_path"])
            self.assertEqual(json.loads(Path(config["telemetry_path"]).read_text())["event"], "render_step")

    def test_random_seed_independent_of_worker_assignment(self):
        key = ("rand_shell", 5000, 120)
        seed = worker.trajectory_seed(42, "scene_010", "Target_001", key)
        self.assertEqual(seed, worker.trajectory_seed(42, "scene_010", "Target_001", list(key)))
        self.assertNotEqual(seed, worker.trajectory_seed(42, "scene_010", "Target_002", key))

    def test_actual_queue_builder_retains_all_actors_for_visibility(self):
        source = ast.parse((MODULE_PATH.parent / "custom_movie_pipeline.py").read_text(encoding="utf-8"))
        cls = next(node for node in source.body if isinstance(node, ast.ClassDef) and node.name == "CustomMoviePipeline")
        method = next(node for node in cls.body if isinstance(node, ast.FunctionDef) and node.name == "start_batch_target_rendering")
        isolated_cls = ast.ClassDef(name="QueueUnderTest", bases=[], keywords=[], body=[method], decorator_list=[])
        namespace = {"render_worker": worker, "unreal": types.SimpleNamespace(log=lambda *args: None),
                     "render_cancellation": types.SimpleNamespace(is_requested=lambda: False)}
        tree = ast.fix_missing_locations(ast.Module(body=[isolated_cls], type_ignores=[]))
        exec(compile(tree, "actual_queue_builder", "exec"), namespace)
        queue = namespace["QueueUnderTest"]
        queue._process_next_target = classmethod(lambda cls: None)
        actors = [types.SimpleNamespace(get_actor_label=lambda label=label: label)
                  for label in ["Target_000", "Target_001", "Target_002"]]
        occluders = [object()]
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "worker.json"
            path.write_text(json.dumps({"target_names": ["Target_001"]}))
            worker.configure_worker(path)
            queue.start_batch_target_rendering("scene_010", actors, occluders, {}, None)
        self.assertEqual(len(queue._target_render_queue), 1)
        task = queue._target_render_queue[0]
        self.assertIs(task["target_actor"], actors[1])
        self.assertIs(task["target_actors"], actors)
        self.assertIs(task["occlusion_actors"], occluders)

    def test_actual_generation_filters_before_creating_assets_preserving_shell_bounds(self):
        source = ast.parse((MODULE_PATH.parent / "utils_sequencer.py").read_text(encoding="utf-8"))
        method = next(node for node in source.body if isinstance(node, ast.FunctionDef) and node.name == "main")
        calls = []
        def generate(**kwargs):
            calls.append(kwargs)
            return "/Game/Map/scene_010", "/Game/Sequences/test"
        cfg = {"global": {"trajectory_types": ["rand_shell", "rot_arc"],
                          "camera_heights": [5000, 12000], "plane_angles": [0, 120], "num_frames": 25}}
        namespace = {"load_trajectory_config": lambda: cfg, "render_worker": worker,
                     "generate_single_trajectory": generate, "random": random,
                     "unreal": types.SimpleNamespace(log=lambda *a: None, log_error=lambda *a: None)}
        exec(compile(ast.Module(body=[method], type_ignores=[]), "actual_generator", "exec"), namespace)
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "worker.json"
            path.write_text(json.dumps({"trajectory_tasks": [["rand_shell", 12000, 120]], "random_seed": 42}))
            worker.configure_worker(path)
            state = random.getstate()
            results = namespace["main"](types.SimpleNamespace(get_actor_label=lambda: "Target_001"), "scene_010")
            self.assertEqual(random.getstate(), state)
        self.assertEqual(len(calls), 1)
        self.assertEqual(set(results), {("rand_shell", 12000, 120)})
        self.assertEqual(calls[0]["trajectory_params"]["min_radius"], 5000)
        self.assertEqual(calls[0]["trajectory_params"]["max_radius"], 12000)

    def test_output_and_sampling_configuration_preserved(self):
        # Geometry now has behavioral coverage in test_trajectory_geometry.py.
        # Keep the unchanged output/sampling checks from the worker integration.
        snapshot = json.loads((Path(__file__).parent / "fixtures" / "render_ast.json").read_text(encoding="utf-8"))
        module = ast.parse((PYTHON_ROOT / "custom_movie_pipeline.py").read_text(encoding="utf-8"))
        cls = next(node for node in module.body if isinstance(node, ast.ClassDef) and node.name == "CustomMoviePipeline")
        current = {node.name: ast.dump(node, include_attributes=False)
                   for node in cls.body if isinstance(node, ast.FunctionDef)}
        for name in ["add_output_config", "create_movie_preset"]:
            with self.subTest(frame_configuration=name):
                self.assertEqual(current[name].replace(", type_params=[]", ""),
                                 snapshot["render_configuration"][name].replace(", type_params=[]", ""))

    def test_capture_overrides_are_attached_to_the_job_without_changing_sampling(self):
        from typing import Optional
        module = ast.parse((PYTHON_ROOT / "custom_movie_pipeline.py").read_text(encoding="utf-8"))
        original = next(node for node in module.body if isinstance(node, ast.ClassDef) and node.name == "CustomMoviePipeline")
        method = next(node for node in original.body if isinstance(node, ast.FunctionDef)
                      and node.name == "add_job_to_queue_with_render_config")
        isolated = ast.ClassDef(name="CaptureUnderTest", bases=[], keywords=[], body=[method], decorator_list=[])
        setting_type = object()
        fake_unreal = types.SimpleNamespace(MoviePipelineConsoleVariableSetting=setting_type, log=lambda *args: None)
        namespace = {"Optional": Optional, "unreal": fake_unreal}
        exec(compile(ast.fix_missing_locations(ast.Module(body=[isolated], type_ignores=[])),
                     "capture_job", "exec"), namespace)
        capture = namespace["CaptureUnderTest"]
        for overrides, single_frame in [({}, False), ({"r.AllowOcclusionQueries": 1,
                "r.HZBOcclusion": 0, "r.NeverOcclusionTestDistance": 1000000}, False),
                ({"r.NeverOcclusionTestDistance": 1000000}, True)]:
            with self.subTest(overrides=overrides, single_frame=single_frame):
                config = {"Render_Passes": [{"pass_name": "rgb", "enable": True, "ext": "png"}],
                          "Resolution": [512, 512], "File_Name_Format": "{frame_number}",
                          "Output_Path": "/tmp/capture", "Motion_Blur": False,
                          "Anti_Alias": {"enable": True, "spatial_samples": 8, "temporal_samples": 1},
                          "Single_Frame_Only": single_frame, "Single_Frame_Index": 12,
                          "Console_Variables": dict(overrides)}
                before = json.dumps(config, sort_keys=True)
                console = mock.Mock()
                preset = mock.Mock()
                preset.find_or_add_setting_by_class.return_value = console
                job = mock.Mock(job_name="capture")
                capture.create_job = mock.Mock(return_value=job)
                capture.create_movie_preset = mock.Mock(return_value=preset)
                self.assertTrue(capture.add_job_to_queue_with_render_config("/Game/Map/test", "/Game/Sequences/test", config))
                capture.create_job.assert_called_once_with("/Game/Map/test", "/Game/Sequences/test")
                job.set_configuration.assert_called_once_with(preset)
                forwarded = capture.create_movie_preset.call_args.kwargs
                self.assertEqual(forwarded["resolution"], [512, 512])
                self.assertEqual(forwarded["anti_alias"], config["Anti_Alias"])
                self.assertEqual(forwarded["render_passes"], config["Render_Passes"])
                self.assertEqual(forwarded["single_frame_index"], 12 if single_frame else None)
                self.assertFalse(forwarded["motion_blur"])
                if overrides:
                    preset.find_or_add_setting_by_class.assert_called_once_with(setting_type)
                    self.assertEqual(console.add_or_update_console_variable.call_args_list,
                                     [mock.call(name, value) for name, value in overrides.items()])
                else:
                    preset.find_or_add_setting_by_class.assert_not_called()
                self.assertEqual(json.dumps(config, sort_keys=True), before)

    def test_actual_occ_gt_builder_keeps_legacy_full_gt_frames(self):
        module = ast.parse((MODULE_PATH.parent / "custom_movie_pipeline.py").read_text(encoding="utf-8"))
        cls = next(node for node in module.body if isinstance(node, ast.ClassDef) and node.name == "CustomMoviePipeline")
        method = next(node for node in cls.body if isinstance(node, ast.FunctionDef) and
                      node.name == "render_multi_trajectory_multi_height_serial")
        steps = []
        class Builder:
            def add_occ_render(self, **kwargs):
                steps.append(("occ", kwargs))
                return self
            def add_gt_render(self, **kwargs):
                steps.append(("gt", kwargs))
                return self
            def build(self):
                return steps
        class Scene:
            def __init__(self, **kwargs):
                self.__dict__.update(kwargs)
        class Controller:
            def __init__(self, *args, **kwargs):
                pass
            def add_render_sequence(self, steps):
                pass
            def start_rendering(self):
                pass
        fake_module = types.SimpleNamespace(RenderController=Controller, RenderSequenceBuilder=Builder, SceneConfig=Scene)
        isolated_cls = ast.ClassDef(name="PipelineUnderTest", bases=[], keywords=[], body=[method], decorator_list=[])
        namespace = {"render_worker": worker, "unreal": types.SimpleNamespace(log=lambda *args: None)}
        exec(compile(ast.fix_missing_locations(ast.Module(body=[isolated_cls], type_ignores=[])),
                     "actual_occ_gt_builder", "exec"), namespace)
        actor = types.SimpleNamespace(get_actor_label=lambda: "Target_001")
        config = {"Single_Frame_Only": False, "Single_Frame_Index": 12, "Resolution": [512, 512]}
        with mock.patch.dict("sys.modules", {"render_controller": fake_module, "batch_utils": types.SimpleNamespace()}):
            namespace["PipelineUnderTest"].render_multi_trajectory_multi_height_serial(
                {("rot_arc", 5000, 0): ("/Game/Map/scene_010", "/Game/Sequences/rot_arc/test")},
                config, "scene_010", "Target_001", [], [actor], actor, None)
        self.assertEqual([kind for kind, _ in steps], ["occ", "gt"])
        for kind, step in steps:
            self.assertIs(step["render_config"]["Single_Frame_Only"], False)
            self.assertEqual(step["render_config"]["Single_Frame_Index"], 12)
            self.assertEqual(step["render_config"]["Resolution"], [512, 512])

    def test_asset_loading_callback_preserves_config_path_ignores_delta(self):
        source = ast.parse((MODULE_PATH.parent / "pipeline.py").read_text(encoding="utf-8"))
        function = next(node for node in source.body if isinstance(node, ast.FunctionDef) and node.name == "start_when_assets_ready")
        state = {"loading": True, "callback": None}
        calls = []
        removed = []
        def register(callback):
            state["callback"] = callback
            return 123
        registry = types.SimpleNamespace(is_loading_assets=lambda: state["loading"])
        fake_unreal = types.SimpleNamespace(
            AssetRegistryHelpers=types.SimpleNamespace(get_asset_registry=lambda: registry),
            register_slate_pre_tick_callback=register,
            unregister_slate_pre_tick_callback=removed.append)
        namespace = {"unreal": fake_unreal, "_pipeline_start_handle": None, "main": calls.append}
        exec(compile(ast.Module(body=[function], type_ignores=[]), "actual_startup", "exec"), namespace)
        namespace["start_when_assets_ready"]("/absolute/render.yaml")
        state["callback"](0.125)
        self.assertEqual(calls, [])
        state["loading"] = False
        state["callback"](0.025)
        state["callback"](0.040)
        self.assertEqual(calls, ["/absolute/render.yaml"])
        self.assertEqual(removed, [123])


if __name__ == "__main__":
    unittest.main()
