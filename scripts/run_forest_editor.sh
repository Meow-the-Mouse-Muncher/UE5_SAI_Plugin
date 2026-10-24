#!/usr/bin/env bash
# Linux UE launcher; retain the caller's external cache and DDC directories.
set -euo pipefail

if [ "$(id -u)" -eq 0 ]; then
    exec runuser -u ue -- "$0" "$@"
fi

forest_editor_bin="${FOREST_EDITOR_BIN:-/opt/UE_5.6_render_fix/Engine/Binaries/Linux/UnrealEditor}"
if [ ! -x "${forest_editor_bin}" ]; then
    printf 'UE editor is not executable: %s\n' "${forest_editor_bin}" >&2
    exit 2
fi
forest_editor_dir="$(cd -- "$(dirname -- "${forest_editor_bin}")" && pwd)"
forest_editor_bin="${forest_editor_dir}/$(basename -- "${forest_editor_bin}")"
cd -- "${forest_editor_dir}"
exec env -u DISPLAY -u WAYLAND_DISPLAY -u SDL_VIDEODRIVER -u PYTHONHOME -u PYTHONPATH -u LD_PRELOAD \
    HOME=/opt/ue-home USER=ue LOGNAME=ue \
    PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin \
    XDG_RUNTIME_DIR=/opt/ue-home/runtime XDG_CACHE_HOME="${XDG_CACHE_HOME:-/opt/ue56-cache}" \
    LD_LIBRARY_PATH=/usr/lib/x86_64-linux-gnu:/usr/local/nvidia/lib64:/usr/local/nvidia/lib \
    VK_ICD_FILENAMES=/opt/ue56-install/nvidia-egl.json \
    VK_DRIVER_FILES=/opt/ue56-install/nvidia-egl.json \
    UE_LocalDataCachePath="${UE_LocalDataCachePath:-/opt/ue56-cache/ddc-render-fixed}" \
    "${forest_editor_bin}" "$@" -vulkan -RenderOffscreen -unattended -nosound -nosplash \
    -stdout -FullStdOutLogOutput -ddc=InstalledNoZenLocalFallback
