#!/usr/bin/env python3
"""
UE5 Python script to batch export Level Sequences to FBX files
可以被其他模块导入调用，也可以独立运行
"""

import unreal
import os
from pathlib import Path

from GLOBAL_VARS import PLUGIN_ROOT


def export_sequence_to_fbx(sequence_path: str, output_dir: str) -> bool:
    """
    Export a single Level Sequence to FBX
    
    Args:
        sequence_path: UE content path, e.g. "/Game/Sequences/fix_line/scene_010_Target_001_height_070_ang_090"
        output_dir: Output directory path
        
    Returns:
        bool: Success or failure
    """
    # Load the sequence asset
    sequence_asset = unreal.EditorAssetLibrary.load_asset(sequence_path)
    if not sequence_asset:
        unreal.log_error(f"Failed to load sequence: {sequence_path}")
        return False
    
    # Get sequence name for output filename
    sequence_name = os.path.basename(sequence_path)
    output_path = os.path.join(output_dir, f"{sequence_name}.fbx")
    
    # Create export parameters
    export_params = unreal.SequencerExportFBXParams()
    export_params.set_editor_property("fbx_file_name", output_path)
    export_params.set_editor_property("sequence", sequence_asset)
    export_params.set_editor_property("world", unreal.EditorLevelLibrary.get_editor_world())
    
    # Export using SequencerTools
    sequencer_tools = unreal.SequencerTools()
    success = sequencer_tools.export_level_sequence_fbx(export_params)
    
    if success:
        unreal.log(f"Exported: {sequence_name} -> {output_path}")
    else:
        unreal.log_error(f"Failed to export: {sequence_name}")
    
    return success


def export_sequences_for_map(
    map_name: str,
    output_base_dir: str = None,
    sequence_paths: list[str] = None,
) -> int:
    """
    Export all sequences for a specific map to FBX
    
    Args:
        map_name: Map name, e.g. "scene_010"
        output_base_dir: Base output directory. If None, uses project's Exported_FBX folder.
        sequence_paths: Exact current-run Level Sequence asset paths. When provided,
            only these assets are exported. The map-name scan is retained solely for
            standalone backward compatibility.
        
    Returns:
        int: Number of successfully exported sequences
    """
    unreal.log(f"[FBX Export] Starting export for map: {map_name}")
    
    # Define paths
    sequences_content_path = "/Game/Sequences"
    
    if output_base_dir is None:
        project_dir = unreal.Paths.project_dir()
        output_base_dir = os.path.join(project_dir, "Exported_FBX")
    
    # Create base output directory
    if not os.path.exists(output_base_dir):
        os.makedirs(output_base_dir)
        unreal.log(f"Created output directory: {output_base_dir}")
    
    # Prefer the exact asset list returned by utils_sequencer for this run. This
    # prevents newly rendered images from being paired with historical FBX files.
    if sequence_paths is None:
        unreal.log_warning("[FBX Export] No current-run sequence list; using legacy map-name scan")
        asset_registry = unreal.AssetRegistryHelpers.get_asset_registry()
        asset_filter = unreal.ARFilter(
            class_names=["LevelSequence"],
            package_paths=[sequences_content_path],
            recursive_paths=True
        )
        asset_paths = [
            str(asset_data.package_name)
            for asset_data in asset_registry.get_assets(asset_filter)
            if map_name in str(asset_data.asset_name)
        ]
    else:
        asset_paths = sorted(set(str(path) for path in sequence_paths))

    if not asset_paths:
        unreal.log_warning(f"No current-run sequences to export for map: {map_name}")
        return 0

    # Group selected sequence assets by category.
    categories = {}
    for asset_path in asset_paths:
        # Extract category from path
        if "/fix_line/" in asset_path:
            category = "fix_line"
        elif "/rot_arc/" in asset_path:
            category = "rot_arc"
        elif "/rot_line/" in asset_path:
            category = "rot_line"
        elif "/plane_grid/" in asset_path:
            category = "plane_grid"
        elif "/rot_spiral/" in asset_path:
            category = "rot_spiral"
        elif "/rand_shell/" in asset_path:
            category = "rand_shell"
        else:
            category = "other"
        
        if category not in categories:
            categories[category] = []
        categories[category].append(asset_path)
    
    selected_count = sum(len(paths) for paths in categories.values())
    unreal.log(f"Selected {selected_count} sequences for map: {map_name}")
    
    # Export each category
    total_exported = 0
    for category, asset_paths in categories.items():
        unreal.log(f"Exporting category: {category} ({len(asset_paths)} sequences)")
        
        # Create category output directory
        category_output_dir = os.path.join(output_base_dir, category)
        if not os.path.exists(category_output_dir):
            os.makedirs(category_output_dir)
        
        # Export sequences in this category
        for asset_path in asset_paths:
            if export_sequence_to_fbx(asset_path, category_output_dir):
                total_exported += 1
    
    unreal.log(f"[FBX Export] Completed: {total_exported}/{selected_count} sequences exported for map: {map_name}")
    return total_exported


def main():
    """
    Main function for standalone execution
    Reads map name from user config or command line
    """
    # Try to get map name from config
    try:
        import json
        config_file = PLUGIN_ROOT / 'misc' / 'user.json'
        with open(config_file, 'r') as f:
            config = json.load(f)
        
        # Support both single map and list of maps
        ue_map = config.get('ue_map', '')
        if isinstance(ue_map, list):
            maps = ue_map
        elif ue_map:
            # Extract map name from path like "/Game/Map/scene_010"
            if '/' in ue_map:
                maps = [ue_map.split('/')[-1]]
            else:
                maps = [ue_map]
        else:
            # Get from 'maps' field
            maps = config.get('maps', [])
        
        if not maps:
            unreal.log_error("No map specified in config. Please set 'ue_map' or 'maps' in user.json")
            return
        
        for map_name in maps:
            export_sequences_for_map(map_name)
            
    except Exception as e:
        unreal.log_error(f"Failed to load config: {e}")
        # Fallback: prompt for map name
        unreal.log_error("Please call export_sequences_for_map('your_map_name') directly")


if __name__ == "__main__":
    main()
