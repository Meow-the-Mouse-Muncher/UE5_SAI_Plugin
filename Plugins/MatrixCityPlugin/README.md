# MatrixCityPlugin

MatrixCityPlugin automates synthetic-data generation inside the current Unreal
project. It creates target-centric camera trajectories, renders occluded (OCC)
and ground-truth (GT) passes through Movie Render Queue, and exports matching
Level Sequences to FBX.

## Documentation

* [Current Quick Start](docs/Get-Started.md)
* [Data Generation Pipeline](docs/DATA_GENERATION_PIPELINE.md)
* [Pipeline Architecture](docs/pipeline_architecture.md)
* [Synthetic Pipeline Audit](docs/SYNTHETIC_PIPELINE_AUDIT.md)
* [Related resources](docs/Related.md)

The first two documents describe the behavior implemented in this checkout.
The architecture and audit documents record design context and known gaps.

## Current entry points

Run from the Unreal project root:

```bash
# Run the configured map.
python Plugins/MatrixCityPlugin/misc/run_cmd_async.py \
  --config_file Plugins/MatrixCityPlugin/misc/user.json

# Export existing sequences without a new image render.
python Plugins/MatrixCityPlugin/misc/run_export_only.py \
  --config_file Plugins/MatrixCityPlugin/misc/user.json \
  --maps scene_010
```

Before running, verify the actor-label, camera, and configuration contracts in
the data-pipeline document. In particular, the current UE pipeline always uses
`misc/render_SAI_config.yaml`, even though the external launcher accepts a
render-config path.
