import unreal
import utils_sequencer
import utils_export
import batch_utils
from GLOBAL_VARS import PLUGIN_ROOT

def main():
    unreal.log_warning("######################################################")
    unreal.log_warning("### EXPORT PIPELINE STARTED ###")
    unreal.log_warning("######################################################")

    # 1. Parse args
    (cmdTokens, cmdSwitches, cmdParameters) = unreal.SystemLibrary.parse_command_line(
        unreal.SystemLibrary.get_command_line()
    )
    target_map_path = cmdParameters.get('target_map', '')
    
    # 2. Load Map
    map_name = ""
    if target_map_path:
        # e.g. /Game/Map/scene_010 -> scene_010
        map_name = target_map_path.split('/')[-1]
        unreal.log(f"Loading map: {target_map_path}")
        batch_utils.load_map_if_different(target_map_path, None)
    else:
        # Use current map
        current_world = unreal.EditorLevelLibrary.get_editor_world()
        map_name = current_world.get_name()
        unreal.log(f"Using current map: {map_name}")

    # 3. Find Target Actors
    target_actors = batch_utils.get_target_actors_by_prefix("Target_")
    unreal.log(f"Found {len(target_actors)} targets in {map_name}.")
    
    if not target_actors:
        unreal.log_error("No targets found! Cannot generate trajectories.")
        # We might still want to export existing sequences if any exist?
        # But usually we want to ensure generation first.
        # Let's proceed to export anyway in case sequences were already generated manually.
    else:
        # 4. Generate Trajectories for each target
        for target_actor in target_actors:
            unreal.log(f"Generating trajectories for target: {target_actor.get_actor_label()}")
            try:
                utils_sequencer.main(target_actor=target_actor, map_name=map_name)
            except Exception as e:
                unreal.log_error(f"Error generating trajectory for {target_actor.get_actor_label()}: {e}")

    # 5. Export to FBX
    unreal.log("Starting batch export...")
    utils_export.batch_export_sequences(map_name=map_name)
    
    unreal.log("Export pipeline finished. Quitting Editor.")
    unreal.SystemLibrary.quit_editor()

if __name__ == "__main__":
    main()
