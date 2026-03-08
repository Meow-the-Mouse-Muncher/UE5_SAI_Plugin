import os
import shutil
import glob

def get_sorted_files(folder_path):
    """
    Returns a sorted list of relevant files (exr, png, jpg, etc.) in the folder.
    """
    if not os.path.exists(folder_path):
        return []
    
    # Check for exr, png, jpg, jpeg
    files = []
    for ext in ['*.exr', '*.png', '*.jpg', '*.jpeg']:
        files.extend(glob.glob(os.path.join(folder_path, ext)))
    
    return sorted(files)

def process_gt_folder(gt_folder, keep_index_method):
    """
    Process GT folder (depth and rgb subfolders).
    keep_index_method: 'middle' or 'last'
    """
    subfolders = ['depth', 'rgb']
    
    for sub in subfolders:
        folder_path = os.path.join(gt_folder, sub)
        files = get_sorted_files(folder_path)
        
        if not files:
            continue
            
        if len(files) == 1:
            print(f"Skipping {folder_path}: Already processed (1 file).")
            continue

        if keep_index_method == 'middle':
            target_index = len(files) // 2
        elif keep_index_method == 'last':
            target_index = len(files) - 1
        else:
            print(f"Unknown method {keep_index_method}")
            return

        target_file = files[target_index]
        print(f"Processing {folder_path}: Keeping {os.path.basename(target_file)} ({keep_index_method} of {len(files)})")
        
        for i, f in enumerate(files):
            if i != target_index:
                try:
                    os.remove(f)
                except OSError as e:
                    print(f"Error deleting {f}: {e}")

def process_occ_folder(occ_folder, keep_index_method):
    """
    Process OCC folder depth maps.
    keep_index_method: 'middle' or 'last' (must match GT rule)
    """
    folder_path = os.path.join(occ_folder, 'depth')
    files = get_sorted_files(folder_path)

    if not files:
        print(f"Skipping {folder_path}: Already processed (empty).")
        return

    if len(files) == 1:
        print(f"Skipping {folder_path}: Already processed (1 file).")
        return

    if keep_index_method == 'middle':
        target_index = len(files) // 2
    elif keep_index_method == 'last':
        target_index = len(files) - 1
    else:
        print(f"Unknown method {keep_index_method}")
        return

    target_file = files[target_index]
    print(f"Processing {folder_path}: Keeping {os.path.basename(target_file)} ({keep_index_method} of {len(files)})")

    for i, f in enumerate(files):
        if i != target_index:
            try:
                os.remove(f)
            except OSError as e:
                print(f"Error deleting {f}: {e}")

def main():
    base_dir = "/home_ssd/sjy/UE5_Project/PCGBiomeForestPoplar/Saved/MovieRenders"
    
    # Datasets to process
    datasets = ['train_data']
    #train_data',
    for dataset in datasets:
        dataset_path = os.path.join(base_dir, dataset)
        if not os.path.exists(dataset_path):
            continue
            
        print(f"Entering dataset: {dataset}")
        
        # Trajectory types
        trajectory_types = os.listdir(dataset_path)
        
        for traj_type in trajectory_types:
            traj_path = os.path.join(dataset_path, traj_type)
            if not os.path.isdir(traj_path):
                continue
                
            print(f"  Processing trajectory: {traj_type}")
            
            # Determine rule
            if traj_type == 'rot_spiral' or traj_type == 'rand_shell':
                rule = 'last'
            else:
                rule = 'middle' # 'plane_grid' and everything else
            
            # Iterate over sequence folders
            # Sequence folders end with _GT or _occ
            # We can group them by name, but treating them independently is easier
            # provided we know which is which.
            
            sequence_folders = os.listdir(traj_path)
            for seq_folder_name in sequence_folders:
                seq_folder_path = os.path.join(traj_path, seq_folder_name)
                
                if not os.path.isdir(seq_folder_path):
                    continue

                if seq_folder_name.endswith('_GT'):
                    process_gt_folder(seq_folder_path, rule)
                elif seq_folder_name.endswith('_occ'):
                    process_occ_folder(seq_folder_path, rule)

if __name__ == "__main__":
    main()
