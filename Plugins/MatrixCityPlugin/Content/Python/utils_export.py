import unreal
import os

def export_sequence_to_fbx(sequence_path, output_dir):
    """
    Export a single Level Sequence to FBX
    """
    # Load the sequence asset
    sequence_asset = unreal.EditorAssetLibrary.load_asset(sequence_path)
    if not sequence_asset:
        unreal.log_error(f"Failed to load sequence: {sequence_path}")
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
        unreal.log(f"Successfully exported: {sequence_name} -> {output_path}")
    else:
        unreal.log_error(f"Failed to export: {sequence_name}")
    
    return success

def batch_export_sequences(map_name=None):
    """
    Batch export all sequences in the Sequences folder with directory structure
    
    Args:
        map_name (str, optional): Filter sequences by map name. If None, exports all.
    """
    
    # Define paths
    sequences_content_path = "/Game/Sequences"
    project_dir = unreal.Paths.project_dir()
    base_output_dir = os.path.join(project_dir, "Content", "Exported_FBX")
    
    # Create base output directory
    if not os.path.exists(base_output_dir):
        os.makedirs(base_output_dir)
        unreal.log(f"Created output directory: {base_output_dir}")
    
    # Get all sequence assets
    asset_registry = unreal.AssetRegistryHelpers.get_asset_registry()
    filter = unreal.ARFilter(
        class_names=["LevelSequence"],
        package_paths=[sequences_content_path],
        recursive_paths=True
    )
    
    sequence_assets = asset_registry.get_assets(filter)
    
    if not sequence_assets:
        unreal.log_warning("No Level Sequences found in /Game/Sequences")
        return
    
    unreal.log(f"Found {len(sequence_assets)} sequences total")
    
    # Filter sequences by map name and group by category
    categories = {}
    filtered_count = 0
    
    for asset_data in sequence_assets:
        asset_name = str(asset_data.asset_name)
        asset_path = str(asset_data.package_name)
        
        # 只处理指定地图的轨迹 (如果指定了 map_name)
        if map_name and map_name not in asset_name:
            continue
            
        filtered_count += 1
        
        # Extract category from path
        # 动态从路径中提取类别 (文件夹名)
        category = "other"
        if asset_path.startswith(sequences_content_path + "/"):
            # remove prefix: /Game/Sequences/Category/Asset -> Category/Asset
            rel_path = asset_path[len(sequences_content_path) + 1:]
            parts = rel_path.split("/")
            if len(parts) > 1:
                category = parts[0]
        
        # 如果动态提取失败，尝试使用关键字（兼容旧逻辑）
        if category == "other":
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
        
        if category not in categories:
            categories[category] = []
        categories[category].append(asset_path)
    
    filter_msg = f" for map: {map_name}" if map_name else ""
    unreal.log(f"Filtered to {filtered_count} sequences{filter_msg}")
    
    if filtered_count == 0:
        unreal.log_warning(f"No sequences found{filter_msg}")
        return
    
    # Export each category
    total_exported = 0
    for category, asset_paths in categories.items():
        unreal.log(f"Exporting category: {category} ({len(asset_paths)} sequences)")
        
        # Create category output directory
        category_output_dir = os.path.join(base_output_dir, category)
        if not os.path.exists(category_output_dir):
            os.makedirs(category_output_dir)
        
        # Export sequences in this category
        for asset_path in asset_paths:
            if export_sequence_to_fbx(asset_path, category_output_dir):
                total_exported += 1
    
    unreal.log(f"Export completed: {total_exported}/{filtered_count} sequences exported successfully{filter_msg}")
