#!/usr/bin/env python3
"""
UE5 Python script to batch export Level Sequences to FBX files
Run this script in UE5 Editor's Python console or as a startup script
"""

import unreal
import os

def export_sequence_to_fbx(sequence_path, output_dir):
    """
    Export a single Level Sequence to FBX
    """
    # Load the sequence asset
    sequence_asset = unreal.EditorAssetLibrary.load_asset(sequence_path)
    if not sequence_asset:
        print(f"Failed to load sequence: {sequence_path}")
        return False
    
    # Get sequence name for output filename
    sequence_name = os.path.basename(sequence_path).replace('.uasset', '')
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
        print(f"Successfully exported: {sequence_name} -> {output_path}")
    else:
        print(f"Failed to export: {sequence_name}")
    
    return success

def batch_export_sequences():
    """
    Batch export all sequences in the Sequences folder with directory structure
    """
    # 手动指定地图名称 - 修改这里来指定不同的地图
    MAP_NAME = "scene_009"
    
    # Define paths
    sequences_content_path = "/Game/Sequences"
    project_dir = unreal.Paths.project_dir()
    base_output_dir = os.path.join(project_dir,"Content", "Exported_FBX_sparse")
    
    # Create base output directory
    if not os.path.exists(base_output_dir):
        os.makedirs(base_output_dir)
        print(f"Created output directory: {base_output_dir}")
    
    # Get all sequence assets
    asset_registry = unreal.AssetRegistryHelpers.get_asset_registry()
    filter = unreal.ARFilter(
        class_names=["LevelSequence"],
        package_paths=[sequences_content_path],
        recursive_paths=True
    )
    
    sequence_assets = asset_registry.get_assets(filter)
    
    if not sequence_assets:
        print("No Level Sequences found in /Game/Sequences")
        return
    
    print(f"Found {len(sequence_assets)} sequences total")
    
    # Filter sequences by map name and group by category
    categories = {}
    filtered_count = 0
    
    for asset_data in sequence_assets:
        asset_path = str(asset_data.package_name)
        asset_name = str(asset_data.asset_name)
        
        # 只处理指定地图的轨迹
        if MAP_NAME not in asset_name:
            continue
            
        filtered_count += 1
        asset_path = str(asset_data.package_name)
        
        # Extract category from path
        if "/fix_line/" in asset_path:
            category = "fix_line"
        elif "/rot_arc/" in asset_path:
            category = "rot_arc"
        elif "/rot_line/" in asset_path:
            category = "rot_line"
        else:
            category = "other"
        
        if category not in categories:
            categories[category] = []
        categories[category].append(asset_path)
    
    print(f"Filtered to {filtered_count} sequences for map: {MAP_NAME}")
    
    if filtered_count == 0:
        print(f"No sequences found for map: {MAP_NAME}")
        return
    
    # Export each category
    total_exported = 0
    for category, asset_paths in categories.items():
        print(f"\nExporting category: {category} ({len(asset_paths)} sequences)")
        
        # Create category output directory
        category_output_dir = os.path.join(base_output_dir, category)
        if not os.path.exists(category_output_dir):
            os.makedirs(category_output_dir)
        
        # Export sequences in this category
        for asset_path in asset_paths:
            if export_sequence_to_fbx(asset_path, category_output_dir):
                total_exported += 1
    
    print(f"\nExport completed: {total_exported}/{filtered_count} sequences exported successfully for map: {MAP_NAME}")

if __name__ == "__main__":
    batch_export_sequences()