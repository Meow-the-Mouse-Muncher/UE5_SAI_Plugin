# Current Quick Start

This guide targets the checked-in Unreal project and the current batch pipeline.
It replaces the old UE 5.0.3/City Sample installation instructions.

## 1. Verify the scene contract

Open the target map under `/Game/Map`. The batch path discovers actor labels,
not Unreal tags:

* every render target must begin with `Target_`;
* occluding `StaticMeshActor` labels must begin with `SM_`;
* the map must contain the configured cine camera, normally
  `CineCameraActor1`.

## 2. Configure the run

Update `Plugins/MatrixCityPlugin/misc/user.json` with paths valid on the current
machine:

* `ue_command`: Unreal Editor executable;
* `ue_project`: the `.uproject` file;
* `ue_map` or `maps`: the map(s) to run;
* `python_script`: normally `Content/Python/pipeline.py`;
* `render_config`: required by the external launcher, but see the next step.

Update `misc/trajectory_config.yaml` to control the trajectory family, heights,
angles, and frame count. Heights are Unreal centimetres.

For the present UE-side pipeline, edit `misc/render_SAI_config.yaml` to set
`Output_Path`, resolution, render passes, anti-aliasing, and `Only_OCC`.
`pipeline.py` currently overrides the command-line config path with this file.

## 3. Run one small smoke test

From the project root, choose one known map before starting a large batch:

```bash
python Plugins/MatrixCityPlugin/misc/run_cmd_async.py \
  --config_file Plugins/MatrixCityPlugin/misc/user.json
```

Check Unreal's `Saved/Logs/Pipeline.log`, the configured output root, and
`Exported_FBX/` after completion. Confirm that expected RGB and depth files are
present for both OCC and GT, unless `Only_OCC` was enabled.

## 4. Scale out only after validation

`run_parallel.py` partitions maps and launches one Unreal process per worker.
The current UE-side socket endpoint is fixed at port `9999`; use one worker
until per-worker socket propagation is fixed and verified.

See [Data Generation Pipeline](DATA_GENERATION_PIPELINE.md) for the full flow
and [Synthetic Pipeline Audit](SYNTHETIC_PIPELINE_AUDIT.md) for known
correctness and reliability limitations.
