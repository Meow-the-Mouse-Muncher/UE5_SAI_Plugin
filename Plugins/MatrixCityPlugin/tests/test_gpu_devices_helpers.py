"""Safety checks for physical GPU mapping and benchmark output accounting."""
import json
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "misc"))
import gpu_devices
import benchmark_render


GPUS = [
    {"index": 0, "uuid": "GPU-a49bfb2d-d9a4-da56-517d-c21de1ff2356", "pci_bus_id": "17:00.0",
     "name": "RTX 3090", "memory_used_mb": 22849, "memory_total_mb": 24576,
     "utilization_gpu_percent": 0},
    {"index": 1, "uuid": "GPU-4426d30c-a516-f476-f276-308ddac5d19c", "pci_bus_id": "65:00.0",
     "name": "RTX 3090", "memory_used_mb": 18, "memory_total_mb": 24576,
     "utilization_gpu_percent": 0},
]


class GPUHelpersTest(unittest.TestCase):
    def test_inventory_parses_units_and_uuid(self):
        response = SimpleNamespace(stdout="1, GPU-4426d30c-a516-f476-f276-308ddac5d19c, 00000000:65:00.0, NVIDIA GeForce RTX 3090, 18, 24576, 0\n")
        with patch.object(gpu_devices.subprocess, "run", return_value=response):
            gpu = gpu_devices.gpu_inventory()[0]
        self.assertEqual(gpu["index"], 1)
        self.assertEqual(gpu["memory_used_mb"], 18)

    def test_index_and_uuid_are_physical_identities(self):
        self.assertEqual(gpu_devices.find_gpu("1", GPUS)["uuid"], GPUS[1]["uuid"])
        self.assertEqual(gpu_devices.find_gpu("GPU-4426d30c", GPUS)["index"], 1)
        with self.assertRaises(ValueError):
            gpu_devices.find_gpu("RTX 3090", GPUS)

    def test_free_gpu_does_not_borrow_memory_occupied_card(self):
        with patch.object(gpu_devices, "gpu_inventory", return_value=GPUS):
            self.assertEqual(gpu_devices.select_free_gpu(), 1)
            with self.assertRaises(RuntimeError):
                gpu_devices.select_free_gpu(exclude=[1])

    def test_uuid_mapping_handles_software_adapter_and_duplicate_gpu_names(self):
        adapters = [{"index": 0, "name": "RTX 3090", "uuid": GPUS[0]["uuid"][4:]},
                    {"index": 1, "name": "llvmpipe", "uuid": "software"},
                    {"index": 2, "name": "RTX 3090", "uuid": GPUS[1]["uuid"][4:]}]
        environment = {"OTHER_SETTING": "preserved", "CUDA_VISIBLE_DEVICES": "0"}
        with patch.object(gpu_devices, "gpu_inventory", return_value=GPUS), \
                patch.object(gpu_devices, "_nvidia_icd_files", return_value="/test/nvidia.json"), \
                patch.object(gpu_devices.subprocess, "run",
                             return_value=SimpleNamespace(stdout=json.dumps(adapters))) as run:
            adapter, launch, selected = gpu_devices.resolve_vulkan_adapter(1, environment)
        self.assertEqual(adapter, 2)
        self.assertEqual(selected["uuid"], GPUS[1]["uuid"])
        self.assertEqual(launch["VK_DRIVER_FILES"], "/test/nvidia.json")
        self.assertEqual(launch["VK_ICD_FILENAMES"], "/test/nvidia.json")
        self.assertEqual(run.call_args.kwargs["env"], launch)
        self.assertNotIn("VK_DRIVER_FILES", environment)

    def test_missing_uuid_fails_instead_of_falling_back(self):
        with patch.object(gpu_devices, "gpu_inventory", return_value=GPUS), \
                patch.object(gpu_devices, "_nvidia_icd_files", return_value="/test/nvidia.json"), \
                patch.object(gpu_devices.subprocess, "run", return_value=SimpleNamespace(stdout="[]")):
            with self.assertRaises(RuntimeError):
                gpu_devices.resolve_vulkan_adapter(1)

    def test_output_counts_include_changed_images_without_double_counting_existing(self):
        before = {"existing/rgb/0000.png": (100, 1), "changed/rgb/0001.png": (200, 1)}
        current = {"existing/rgb/0000.png": (100, 1), "changed/rgb/0001.png": (200, 2),
                   "new/depth/0000.exr": (400, 3), "new/normal/0000.exr": (500, 3)}
        totals = benchmark_render.output_totals(before, current)
        self.assertEqual(totals, {"rgb_png": 1, "depth_exr": 1, "other_exr": 1,
                                  "image_files": 3, "image_bytes": 1100})

    def test_ownership_rejects_reused_root_pid(self):
        table = {100: {"ppid": 1, "pgrp": 100, "start": 20},
                 200: {"ppid": 100, "pgrp": 100, "start": 21}}
        with patch.object(benchmark_render, "_process_table", return_value=table):
            owned, _ = benchmark_render._update_owned(100, {100: 10})
        self.assertFalse(owned)

    def test_ownership_tracks_only_own_descendants(self):
        table = {100: {"ppid": 1, "pgrp": 100, "start": 10},
                 101: {"ppid": 100, "pgrp": 101, "start": 11},
                 102: {"ppid": 101, "pgrp": 101, "start": 12},
                 200: {"ppid": 1, "pgrp": 200, "start": 20}}
        with patch.object(benchmark_render, "_process_table", return_value=table):
            owned, _ = benchmark_render._update_owned(100, {})
        self.assertEqual(owned, {100, 101, 102})

    def test_small_idle_graphics_context_is_allowed_only_alongside_target_context(self):
        display = {"gpu_index": 0, "pid": 123, "type": "G",
                   "framebuffer_mb": 4, "sm_util_percent": None}
        target = {"gpu_index": 1, "pid": 123, "type": "C+G",
                  "framebuffer_mb": 5000, "sm_util_percent": 80}
        self.assertEqual(benchmark_render.placement_violations([display, target], 1), [])
        self.assertTrue(benchmark_render.placement_violations([display], 1))
        self.assertEqual(benchmark_render.placement_violations(
            [display], 1, verified_target_pids={123}), [])

    def test_meaningful_wrong_gpu_allocation_or_activity_is_rejected(self):
        target = {"gpu_index": 1, "pid": 123, "type": "C+G",
                  "framebuffer_mb": 5000, "sm_util_percent": 80}
        base = {"gpu_index": 0, "pid": 123, "type": "G",
                "framebuffer_mb": 4, "sm_util_percent": 0}
        for overrides in ({"framebuffer_mb": 129}, {"sm_util_percent": 1},
                          {"type": "C+G"}, {"framebuffer_mb": None}):
            with self.subTest(overrides=overrides):
                self.assertTrue(benchmark_render.placement_violations([{**base, **overrides}, target], 1))
                self.assertTrue(benchmark_render.placement_violations(
                    [{**base, **overrides}], 1, verified_target_pids={123}))

    def test_pmon_parses_per_process_framebuffer_and_activity(self):
        data = ("# gpu pid type sm mem enc dec jpg ofa fb ccpm command\n"
                "# Idx # C/G % % % % % % MB MB name\n"
                "0 123 G - - - - - - 0 0 UnrealEditor\n"
                "1 123 C+G 76 35 - - - - 5100 0 UnrealEditor\n")
        with patch.object(benchmark_render.subprocess, "run", return_value=SimpleNamespace(stdout=data)):
            processes = benchmark_render.gpu_processes(GPUS)
        self.assertEqual(processes[0]["framebuffer_mb"], 0)
        self.assertIsNone(processes[0]["sm_util_percent"])
        self.assertEqual(processes[1]["sm_util_percent"], 76)
        self.assertEqual(benchmark_render.placement_violations(processes, 1), [])


if __name__ == "__main__":
    unittest.main()
