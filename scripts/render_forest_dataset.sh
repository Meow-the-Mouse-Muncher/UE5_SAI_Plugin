#!/usr/bin/env bash
# Start or resume train + test; extra CLI arguments override the defaults below.
# Example: bash scripts/render_forest_dataset.sh --dry-run
# A different machine can set FOREST_CONFIG, FOREST_RUNTIME_ROOT and FOREST_RUN_DIR.
set -euo pipefail

forest_script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
forest_project_root="$(cd -- "${forest_script_dir}/.." && pwd)"
forest_runtime_root="${FOREST_RUNTIME_ROOT:-/mnt/cpfs_b5/perception_data/sjy/forest_runtime/PCGBiomeForestPoplar}"
export FOREST_RUNTIME_ROOT="${forest_runtime_root}"
forest_run_dir="${FOREST_RUN_DIR:-${forest_runtime_root}/Saved/RenderRuns/root_cause_20261020_235808/production_fresh_preparation/execution}"
forest_output_root="${FOREST_OUTPUT_ROOT:-/mnt/cpfs_b5/perception_data/sjy/forest_dataset}"
forest_config="${FOREST_CONFIG:-${forest_project_root}/Plugins/MatrixCityPlugin/misc/user.json}"
forest_vram_gib="${FOREST_VRAM_BUDGET_GB:-100}"

exec "${FOREST_PYTHON:-python3}" "${forest_script_dir}/render_forest_dataset.py" \
  --config "${forest_config}" \
  --run-dir "${forest_run_dir}" \
  --output-root "${forest_output_root}" \
  --resume \
  --gpu-ids "${FOREST_GPU_IDS:-auto}" \
  --workers-per-gpu auto \
  --vram-budget-gb "${forest_vram_gib}" \
  "$@"
