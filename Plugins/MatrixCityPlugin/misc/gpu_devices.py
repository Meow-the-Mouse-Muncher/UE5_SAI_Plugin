"""Map NVIDIA physical GPU identities to the Vulkan adapters UE actually sees.

CUDA_VISIBLE_DEVICES does not constrain Vulkan.  Probe using the same driver
environment as the future UE process and match device UUIDs, never device names.
Only the Python standard library and nvidia-smi are required.
"""
import argparse
import csv
import ctypes as ct
import glob
import json
import os
from pathlib import Path
import subprocess
import sys
import uuid


def gpu_inventory():
    """Return NVIDIA physical GPU indices, UUIDs and current load."""
    fields = ("index", "uuid", "pci.bus_id", "name", "memory.used",
              "memory.total", "utilization.gpu")
    result = subprocess.run(
        ["nvidia-smi", "--query-gpu=" + ",".join(fields),
         "--format=csv,noheader,nounits"],
        check=True, capture_output=True, text=True, timeout=15)
    inventory = []
    for row in csv.reader(result.stdout.splitlines()):
        if not row:
            continue
        row = [value.strip() for value in row]
        if len(row) != len(fields):
            raise RuntimeError("Unexpected nvidia-smi inventory response")
        inventory.append({
            "index": int(row[0]), "uuid": row[1], "pci_bus_id": row[2],
            "name": row[3], "memory_used_mb": _number(row[4]),
            "memory_total_mb": _number(row[5]),
            "utilization_gpu_percent": _number(row[6]),
        })
    if not inventory:
        raise RuntimeError("nvidia-smi reported no NVIDIA GPUs")
    return inventory


def _number(value):
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _identity(value):
    return str(value).lower().removeprefix("gpu-")


def find_gpu(gpu_id, inventory=None):
    """Resolve a physical nvidia-smi index or unambiguous full/prefix UUID."""
    inventory = inventory if inventory is not None else gpu_inventory()
    value = str(gpu_id).strip()
    if value.isdigit():
        matches = [gpu for gpu in inventory if gpu["index"] == int(value)]
    else:
        value = _identity(value)
        matches = [gpu for gpu in inventory
                   if len(value) >= 8 and _identity(gpu["uuid"]).startswith(value)]
    if len(matches) != 1:
        raise ValueError("GPU {!r} must identify exactly one NVIDIA GPU".format(gpu_id))
    return dict(matches[0])


def select_free_gpu(max_memory_mb=1024, max_utilization=10, exclude=None):
    """Choose a lightly used GPU, or fail rather than borrow a busy GPU."""
    inventory = gpu_inventory()
    excluded = {find_gpu(value, inventory)["uuid"] for value in (exclude or [])}
    candidates = [gpu for gpu in inventory
                  if gpu["uuid"] not in excluded
                  and gpu["memory_used_mb"] is not None
                  and gpu["utilization_gpu_percent"] is not None
                  and gpu["memory_used_mb"] <= max_memory_mb
                  and gpu["utilization_gpu_percent"] <= max_utilization]
    if not candidates:
        raise RuntimeError("No idle GPU meets memory/utilization limits")
    candidates.sort(key=lambda gpu: (gpu["memory_used_mb"],
                                    gpu["utilization_gpu_percent"], gpu["index"]))
    return candidates[0]["index"]


def _nvidia_icd_files(env):
    candidates = []
    for variable in ("VK_DRIVER_FILES", "VK_ICD_FILENAMES"):
        candidates.extend(env.get(variable, "").split(os.pathsep))
    candidates.extend(glob.glob("/usr/share/vulkan/icd.d/*nvidia*.json"))
    candidates.extend(glob.glob("/etc/vulkan/icd.d/*nvidia*.json"))
    selected = []
    for value in candidates:
        if not value or value in selected:
            continue
        path = Path(value)
        if not path.is_file():
            continue
        try:
            definition = json.loads(path.read_text())
            library = str(definition.get("ICD", {}).get("library_path", ""))
        except (OSError, ValueError):
            continue
        if "nvidia" in library.lower():
            selected.append(str(path.resolve()))
    if not selected:
        raise RuntimeError("No NVIDIA Vulkan ICD JSON found; refusing an unverified adapter")
    # One driver definition is sufficient. Duplicate definitions can duplicate
    # adapters, so use the first valid definition under the launch environment.
    return selected[0]


def resolve_vulkan_adapter(gpu_id, env=None):
    """Return (raw Vulkan index, launch environment, physical GPU details).

    ``gpu_id`` is an nvidia-smi index or NVIDIA UUID, not a Vulkan index.
    The probe runs in a new process because a loaded Vulkan loader caches its
    environment. Pass the returned environment unchanged to the UE subprocess.
    """
    gpu = find_gpu(gpu_id)
    launch_env = dict(os.environ if env is None else env)
    driver = _nvidia_icd_files(launch_env)
    # Modern and old Vulkan loaders use different names for this override.
    launch_env["VK_DRIVER_FILES"] = driver
    launch_env["VK_ICD_FILENAMES"] = driver
    result = subprocess.run(
        [sys.executable, str(Path(__file__).resolve()), "--_probe-vulkan"],
        env=launch_env, check=True, capture_output=True, text=True, timeout=30)
    try:
        adapters = json.loads(result.stdout)
    except ValueError as exc:
        raise RuntimeError("Vulkan probe returned invalid JSON: " + result.stdout[:200]) from exc
    matches = [adapter for adapter in adapters
               if _identity(adapter["uuid"]) == _identity(gpu["uuid"])]
    if len(matches) != 1:
        raise RuntimeError("NVIDIA GPU {} has {} UUID matches in Vulkan: {}".format(
            gpu["uuid"], len(matches), adapters))
    adapter = matches[0]
    gpu.update({"vulkan_index": adapter["index"], "vulkan_name": adapter["name"],
                "vulkan_uuid": adapter["uuid"], "vulkan_icd": driver})
    return adapter["index"], launch_env, gpu


def _probe_vulkan():
    """Enumerate raw Vulkan order and VkPhysicalDeviceIDProperties UUIDs."""
    class ApplicationInfo(ct.Structure):
        _fields_ = [("sType", ct.c_uint32), ("pNext", ct.c_void_p),
                    ("name", ct.c_char_p), ("version", ct.c_uint32),
                    ("engine", ct.c_char_p), ("engineVersion", ct.c_uint32),
                    ("apiVersion", ct.c_uint32)]

    class InstanceCreateInfo(ct.Structure):
        _fields_ = [("sType", ct.c_uint32), ("pNext", ct.c_void_p),
                    ("flags", ct.c_uint32), ("application", ct.POINTER(ApplicationInfo)),
                    ("layerCount", ct.c_uint32), ("layers", ct.c_void_p),
                    ("extensionCount", ct.c_uint32), ("extensions", ct.c_void_p)]

    class DeviceID(ct.Structure):
        _fields_ = [("sType", ct.c_uint32), ("pNext", ct.c_void_p),
                    ("deviceUUID", ct.c_ubyte * 16), ("driverUUID", ct.c_ubyte * 16),
                    ("deviceLUID", ct.c_ubyte * 8), ("nodeMask", ct.c_uint32),
                    ("luidValid", ct.c_uint32)]

    class Properties2(ct.Structure):
        # VkPhysicalDeviceProperties is smaller than this buffer on the Linux
        # 64-bit ABI. Only its fixed-layout deviceName prefix is read below.
        _fields_ = [("sType", ct.c_uint32), ("pNext", ct.c_void_p),
                    ("properties", ct.c_ubyte * 4096)]

    vk = ct.CDLL("libvulkan.so.1")
    vk.vkCreateInstance.argtypes = [ct.POINTER(InstanceCreateInfo), ct.c_void_p,
                                   ct.POINTER(ct.c_void_p)]
    vk.vkCreateInstance.restype = ct.c_int32
    vk.vkEnumeratePhysicalDevices.argtypes = [ct.c_void_p, ct.POINTER(ct.c_uint32), ct.c_void_p]
    vk.vkEnumeratePhysicalDevices.restype = ct.c_int32
    vk.vkGetPhysicalDeviceProperties2.argtypes = [ct.c_void_p, ct.POINTER(Properties2)]
    vk.vkGetPhysicalDeviceProperties2.restype = None
    vk.vkDestroyInstance.argtypes = [ct.c_void_p, ct.c_void_p]
    vk.vkDestroyInstance.restype = None
    app = ApplicationInfo(0, None, b"UE GPU UUID probe", 0, b"", 0, (1 << 22) | (1 << 12))
    create = InstanceCreateInfo(1, None, 0, ct.pointer(app), 0, None, 0, None)
    instance = ct.c_void_p()
    code = vk.vkCreateInstance(ct.byref(create), None, ct.byref(instance))
    if code != 0:
        raise RuntimeError("vkCreateInstance failed with VkResult {}".format(code))
    try:
        count = ct.c_uint32()
        code = vk.vkEnumeratePhysicalDevices(instance, ct.byref(count), None)
        if code != 0 or not count.value:
            raise RuntimeError("Vulkan found no devices (VkResult {})".format(code))
        devices = (ct.c_void_p * count.value)()
        code = vk.vkEnumeratePhysicalDevices(instance, ct.byref(count), devices)
        if code != 0:
            raise RuntimeError("Vulkan enumeration failed with VkResult {}".format(code))
        adapters = []
        for index, device in enumerate(devices[:count.value]):
            identity = DeviceID()
            identity.sType = 1000071004  # VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_ID_PROPERTIES
            properties = Properties2()
            properties.sType = 1000059001  # VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_PROPERTIES_2
            properties.pNext = ct.addressof(identity)
            vk.vkGetPhysicalDeviceProperties2(device, ct.byref(properties))
            raw = bytes(properties.properties)
            name = raw[20:276].split(b"\0", 1)[0].decode("utf-8", "replace")
            adapters.append({"index": index, "name": name,
                             "uuid": str(uuid.UUID(bytes=bytes(identity.deviceUUID)))})
        return adapters
    finally:
        vk.vkDestroyInstance(instance, None)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--gpu-id", default=None, help="physical NVIDIA index or UUID")
    parser.add_argument("--auto", action="store_true", help="choose an idle GPU")
    parser.add_argument("--max-memory-mb", type=float, default=1024)
    parser.add_argument("--max-utilization", type=float, default=10)
    parser.add_argument("--_probe-vulkan", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args._probe_vulkan:
        print(json.dumps(_probe_vulkan()))
        return
    result = {"gpus": gpu_inventory()}
    gpu_id = args.gpu_id
    if args.auto:
        gpu_id = select_free_gpu(args.max_memory_mb, args.max_utilization)
    if gpu_id is not None:
        index, env, device = resolve_vulkan_adapter(gpu_id)
        result.update({"selected": device, "graphicsadapter": index,
                       "launch_environment": {key: env[key]
                                              for key in ("VK_DRIVER_FILES", "VK_ICD_FILENAMES")}})
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
