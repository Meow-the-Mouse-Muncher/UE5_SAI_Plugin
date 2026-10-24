"""Compatibility entry point for dynamic scene/trajectory render scheduling.

Existing --num_gpus, --gpu_ids and --processes_per_gpu flags remain accepted.
"""
import sys

if __name__ == '__main__':
    sys.dont_write_bytecode = True

from render_dataset import main


if __name__ == "__main__":
    raise SystemExit(main())
