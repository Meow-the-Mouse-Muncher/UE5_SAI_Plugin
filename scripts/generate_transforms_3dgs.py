import argparse
import os
import json
import numpy as np
import cv2
from pathlib import Path

def parse_args():
    parser = argparse.ArgumentParser(description="Generate 3DGS transforms.json from UE plugin output.")
    parser.add_argument("--input_dir", type=str, required=True, help="Path to the directory containing transforms.json and images")
    parser.add_argument("--scale", type=float, default=100, help="Scale factor (default: 100)")
    return parser.parse_args()

def main():
    args = parse_args()
    input_dir = Path(args.input_dir)
    
    if not input_dir.exists():
        print(f"Error: Input directory {input_dir} does not exist.")
        return

    transforms_file = input_dir / "transforms.json"
    if not transforms_file.exists():
        print(f"Error: transforms.json not found in {input_dir}")
        return

    print(f"Processing {transforms_file}...")

    with open(transforms_file, "r") as f:
        tj = json.load(f)

    all_frames = []
    frames = tj.get('frames', [])
    
    # Determine image resolution from the first available image
    # Default fallback values if no image is found
    w = 1920.0 
    h = 1080.0
    found_res = False

    for frame in frames:
        # Assuming images are named by frame_index.zfill(4) + .png as in reference code
        image_name = str(frame['frame_index']).zfill(4) + '.png'
        image_path_abs = input_dir / image_name
        
        # Try to read resolution from the first valid image found
        if not found_res and image_path_abs.exists():
            try:
                im = cv2.imread(str(image_path_abs))
                if im is not None:
                    h_img, w_img = im.shape[:2]
                    w = float(w_img)
                    h = float(h_img)
                    found_res = True
                    print(f"Detected resolution from {image_name}: {int(w)}x{int(h)}")
            except Exception as e:
                print(f"Warning: Could not read image {image_path_abs} to determine resolution: {e}")

        # Relative path from pose/transforms.json to image file
        # Output file will be in: input_dir/pose/transforms.json
        # Image file is in:       input_dir/rgb/image_name
        # So relative path is:    ../image_name
        file_path = os.path.join("..","rgb",image_name)

        c2w = np.array(frame['rot_mat'])
        
        # Transformation logic strictly from reference (load_aerial.py / load_street.py)
        c2w[:3, :3] *= 100
        # c2w[:3, 3] /= args.scale
        
        all_frames.append({
            'file_path': file_path,
            'transform_matrix': c2w.tolist()
        })

    if not found_res:
        print(f"Warning: Could not determine resolution from images. Using default {int(w)}x{int(h)}.")

    # Camera intrinsics
    angle_x = tj['camera_angle_x']
    fl_x = float(.5 * w / np.tan(.5 * angle_x))
    fl_y = fl_x
    
    # Distortion parameters (default to 0 as in reference)
    k1 = 0
    k2 = 0
    k3 = 0
    k4 = 0
    p1 = 0
    p2 = 0
    cx = w / 2
    cy = h / 2
    
    pose = {
        "camera_angle_x": angle_x,
        "fl_x": fl_x,
        "fl_y": fl_y,
        "k1": k1,
        "k2": k2,
        "k3": k3,
        "k4": k4,
        "p1": p1,
        "p2": p2,
        "cx": cx,
        "cy": cy,
        "w": w,
        "h": h,
        "frames": all_frames
    }
    
    # Create pose directory
    pose_dir = input_dir / "pose"
    pose_dir.mkdir(exist_ok=True)
    
    output_file = pose_dir / "transforms.json"
    with open(output_file, "w") as outfile:
        json.dump(pose, outfile, indent=2)
        
    print(f"Successfully generated {output_file} with {len(all_frames)} frames.")

if __name__ == "__main__":
    main()
