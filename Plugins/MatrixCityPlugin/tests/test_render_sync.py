"""Failure propagation around the actual UE queue-completion callback."""
import ast
from pathlib import Path
import types
import unittest
from unittest import mock


PLUGIN_ROOT = Path(__file__).resolve().parents[1]
PYTHON_ROOT = PLUGIN_ROOT / "Content" / "Python"
if not PYTHON_ROOT.is_dir():
    PYTHON_ROOT = PLUGIN_ROOT / "python"
SOURCE = PYTHON_ROOT / "custom_movie_pipeline.py"


class RenderSyncTests(unittest.TestCase):
    def make_pipeline(self, bridge=True, result=True):
        self.calls = []
        self.messages = []
        self.logs = []
        self.render_results = []
        executor = types.SimpleNamespace(send_socket_message=self.messages.append)
        system = types.SimpleNamespace(collect_garbage=lambda: self.calls.append("gc_request"),
                                       quit_editor=lambda: self.calls.append("quit"))
        unreal = types.SimpleNamespace(
            SystemLibrary=system, MoviePipelineLinearExecutorBase=object,
            log=self.logs.append, log_warning=self.logs.append, log_error=self.logs.append)
        if bridge:
            def native_sync(wait):
                self.calls.append(("sync", wait))
                return result
            unreal.MatrixCityRenderSyncLibrary = types.SimpleNamespace(synchronize_rendering=native_sync)
        source = ast.parse(SOURCE.read_text(encoding="utf-8"))
        original = next(node for node in source.body if isinstance(node, ast.ClassDef) and node.name == "CustomMoviePipeline")
        names = {"_synchronize_rendering", "_abort_render_sync_failure", "_flush_gpu_and_wait",
                 "onQueueFinishedCallback", "_on_map_targets_completed"}
        cls = ast.ClassDef(name="CustomMoviePipeline", bases=[], keywords=[], decorator_list=[],
                           body=[node for node in original.body if isinstance(node, ast.FunctionDef) and node.name in names])
        namespace = {"unreal": unreal,
                     "render_cancellation": types.SimpleNamespace(is_requested=lambda: False)}
        exec(compile(ast.fix_missing_locations(ast.Module(body=[cls], type_ignores=[])),
                     "actual_render_sync", "exec"), namespace)
        pipeline = namespace["CustomMoviePipeline"]
        pipeline._batch_failures = []
        pipeline._is_batch_target_rendering = True
        pipeline.new_executor = executor
        pipeline._render_controller = types.SimpleNamespace(
            performance_settings={"queue_wait_cycles": 1, "queue_wait_seconds": .25,
                                  "queue_collect_garbage": True},
            get_current_step=lambda: "step",
            _on_render_complete=lambda step, success: self.render_results.append((step, success)))
        return pipeline, executor

    def test_missing_bridge_reports_failure_and_exits_without_fake_success(self):
        pipeline, executor = self.make_pipeline(bridge=False)
        with mock.patch("time.sleep") as sleep:
            pipeline.onQueueFinishedCallback(executor, True)
        self.assertEqual(self.messages, ["Pipeline Failed"])
        self.assertEqual(self.calls, ["quit"])
        self.assertEqual(self.render_results, [])
        self.assertFalse(pipeline._is_batch_target_rendering)
        self.assertFalse(any("GPU flush completed in callback" in line for line in self.logs))
        sleep.assert_not_called()

    def test_native_rejection_reports_failure(self):
        pipeline, executor = self.make_pipeline(result=False)
        with mock.patch("time.sleep") as sleep:
            pipeline.onQueueFinishedCallback(executor, True)
        self.assertEqual(self.messages, ["Pipeline Failed"])
        self.assertEqual(self.calls, [("sync", True), "quit"])
        self.assertEqual(self.render_results, [])
        sleep.assert_not_called()

    def test_native_exception_reports_failure_and_exits(self):
        pipeline, executor = self.make_pipeline()
        unreal = pipeline._synchronize_rendering.__globals__["unreal"]
        with mock.patch.object(unreal.MatrixCityRenderSyncLibrary, "synchronize_rendering",
                               side_effect=RuntimeError("native test failure")), mock.patch("time.sleep") as sleep:
            pipeline.onQueueFinishedCallback(executor, True)
        self.assertEqual(self.messages, ["Pipeline Failed"])
        self.assertEqual(self.calls, ["quit"])
        self.assertEqual(self.render_results, [])
        sleep.assert_not_called()

    def test_map_completion_sync_failure_cannot_send_finished(self):
        pipeline, executor = self.make_pipeline(bridge=False)
        pipeline._current_map_context = {"map_name": "scene_010", "executor": executor,
                                         "performance_settings": {"map_wait_cycles": 3, "map_wait_seconds": 1}}
        with mock.patch("time.sleep") as sleep:
            pipeline._on_map_targets_completed()
        self.assertEqual(self.messages, ["Pipeline Failed"])
        self.assertEqual(self.calls, ["quit"])
        sleep.assert_not_called()

    def test_native_success_preserves_balanced_delay_and_render_completion(self):
        pipeline, executor = self.make_pipeline()
        with mock.patch("time.sleep") as sleep:
            pipeline.onQueueFinishedCallback(executor, True)
        self.assertEqual(self.calls, [("sync", True), "gc_request"])
        self.assertEqual(self.render_results, [("step", True)])
        self.assertEqual(self.messages, [])
        sleep.assert_called_once_with(.25)

    def test_zero_wait_cycles_still_requires_a_real_barrier(self):
        pipeline, _ = self.make_pipeline()
        with mock.patch("time.sleep") as sleep:
            pipeline._flush_gpu_and_wait(wait_cycles=0, cycle_time=1)
        self.assertEqual(self.calls, [("sync", True), "gc_request"])
        sleep.assert_not_called()


if __name__ == "__main__":
    unittest.main()
