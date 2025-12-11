"""
Output Manager for Pipeline Automation System.

Handles output directory structure, file naming conventions, and storage management
for the automated data collection pipeline.
"""

import os
import shutil
from pathlib import Path
from typing import Dict, List, Optional, Tuple
from dataclasses import dataclass
from datetime import datetime

from .base_manager import BaseManager
from ..models import MapInfo, TargetObject


@dataclass
class OutputConfig:
    """Configuration for output management."""
    base_output_dir: str
    fbx_subdir: str = "trajectories"
    images_subdir: str = "images"
    logs_subdir: str = "logs"
    min_free_space_gb: float = 10.0
    max_files_per_dir: int = 10000
    cleanup_old_files: bool = True
    retention_days: int = 30


class OutputManager(BaseManager):
    """
    Manages output directory structure and file operations.
    
    Creates hierarchical directory structure: base/map/target/height/mode/
    Handles file naming conventions and storage space validation.
    """
    
    def __init__(self, config: OutputConfig):
        super().__init__()
        self.config = config
        self.base_path = Path(config.base_output_dir)
        self._ensure_base_directory()
        
    def _ensure_base_directory(self) -> None:
        """Create base output directory if it doesn't exist."""
        try:
            self.base_path.mkdir(parents=True, exist_ok=True)
            self.logger.info(f"Output base directory: {self.base_path}")
        except Exception as e:
            self.logger.error(f"Failed to create base directory {self.base_path}: {e}")
            raise
            
    def get_output_path(self, map_info: MapInfo, target: TargetObject, 
                       height_offset: float, mode: str, 
                       file_type: str = "fbx") -> Path:
        """
        Generate hierarchical output path for a specific sampling session.
        
        Args:
            map_info: Map information
            target: Target object information
            height_offset: Height variant offset
            mode: Sampling mode ("OCC" or "GT")
            file_type: File type for subdirectory selection
            
        Returns:
            Complete path for the output file
        """
        # Create directory structure: base/map/target/height/mode/
        map_name = self._sanitize_name(map_info.name)
        target_name = self._sanitize_name(target.name)
        height_str = f"h{height_offset:+.1f}".replace(".", "_")
        
        # Select subdirectory based on file type
        if file_type.lower() == "fbx":
            subdir = self.config.fbx_subdir
        elif file_type.lower() in ["png", "jpg", "jpeg"]:
            subdir = self.config.images_subdir
        else:
            subdir = "misc"
            
        output_dir = (self.base_path / map_name / target_name / 
                     height_str / mode.upper() / subdir)
        
        return output_dir
        
    def create_session_directory(self, map_info: MapInfo, target: TargetObject,
                               height_offset: float, mode: str) -> Dict[str, Path]:
        """
        Create all necessary directories for a sampling session.
        
        Args:
            map_info: Map information
            target: Target object information  
            height_offset: Height variant offset
            mode: Sampling mode ("OCC" or "GT")
            
        Returns:
            Dictionary mapping file types to their output directories
        """
        directories = {}
        
        try:
            # Create directories for different file types
            for file_type in ["fbx", "png", "log"]:
                output_dir = self.get_output_path(map_info, target, height_offset, 
                                                mode, file_type)
                output_dir.mkdir(parents=True, exist_ok=True)
                directories[file_type] = output_dir
                
            self.logger.info(f"Created session directories for {map_info.name}/"
                           f"{target.name}/h{height_offset:+.1f}/{mode}")
            return directories
            
        except Exception as e:
            self.logger.error(f"Failed to create session directories: {e}")
            raise
            
    def generate_filename(self, map_info: MapInfo, target: TargetObject,
                         height_offset: float, mode: str, file_type: str,
                         sequence_id: Optional[str] = None) -> str:
        """
        Generate standardized filename for output files.
        
        Args:
            map_info: Map information
            target: Target object information
            height_offset: Height variant offset
            mode: Sampling mode ("OCC" or "GT")
            file_type: File extension
            sequence_id: Optional sequence identifier
            
        Returns:
            Standardized filename
        """
        # Format: MapName_TargetName_H+1.5_OCC_20241211_143022[_SeqID].ext
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        
        map_name = self._sanitize_name(map_info.name)
        target_name = self._sanitize_name(target.name)
        height_str = f"H{height_offset:+.1f}".replace(".", "_")
        
        parts = [map_name, target_name, height_str, mode.upper(), timestamp]
        
        if sequence_id:
            parts.append(self._sanitize_name(sequence_id))
            
        filename = "_".join(parts) + f".{file_type.lower()}"
        return filename
        
    def get_full_filepath(self, map_info: MapInfo, target: TargetObject,
                         height_offset: float, mode: str, file_type: str,
                         sequence_id: Optional[str] = None) -> Path:
        """
        Generate complete filepath including directory and filename.
        
        Returns:
            Complete path to the output file
        """
        output_dir = self.get_output_path(map_info, target, height_offset, 
                                        mode, file_type)
        filename = self.generate_filename(map_info, target, height_offset,
                                        mode, file_type, sequence_id)
        return output_dir / filename
        
    def check_storage_space(self) -> Tuple[bool, float]:
        """
        Check available storage space in the output directory.
        
        Returns:
            Tuple of (sufficient_space, available_gb)
        """
        try:
            stat = shutil.disk_usage(self.base_path)
            available_gb = stat.free / (1024**3)
            sufficient = available_gb >= self.config.min_free_space_gb
            
            if not sufficient:
                self.logger.warning(f"Low storage space: {available_gb:.1f}GB "
                                  f"(minimum: {self.config.min_free_space_gb}GB)")
            
            return sufficient, available_gb
            
        except Exception as e:
            self.logger.error(f"Failed to check storage space: {e}")
            return False, 0.0
            
    def cleanup_old_files(self, days: Optional[int] = None) -> int:
        """
        Clean up old files based on retention policy.
        
        Args:
            days: Retention period in days (uses config default if None)
            
        Returns:
            Number of files cleaned up
        """
        if not self.config.cleanup_old_files:
            return 0
            
        retention_days = days or self.config.retention_days
        cutoff_time = datetime.now().timestamp() - (retention_days * 24 * 3600)
        
        cleaned_count = 0
        
        try:
            for root, dirs, files in os.walk(self.base_path):
                for file in files:
                    filepath = Path(root) / file
                    if filepath.stat().st_mtime < cutoff_time:
                        try:
                            filepath.unlink()
                            cleaned_count += 1
                        except Exception as e:
                            self.logger.warning(f"Failed to delete {filepath}: {e}")
                            
            if cleaned_count > 0:
                self.logger.info(f"Cleaned up {cleaned_count} old files "
                               f"(older than {retention_days} days)")
                
        except Exception as e:
            self.logger.error(f"Error during cleanup: {e}")
            
        return cleaned_count
        
    def validate_directory_limits(self, directory: Path) -> bool:
        """
        Check if directory exceeds file count limits.
        
        Args:
            directory: Directory to check
            
        Returns:
            True if within limits, False otherwise
        """
        try:
            if not directory.exists():
                return True
                
            file_count = len(list(directory.iterdir()))
            
            if file_count >= self.config.max_files_per_dir:
                self.logger.warning(f"Directory {directory} has {file_count} files "
                                  f"(limit: {self.config.max_files_per_dir})")
                return False
                
            return True
            
        except Exception as e:
            self.logger.error(f"Failed to validate directory limits: {e}")
            return False
            
    def get_session_summary(self, map_info: MapInfo, target: TargetObject,
                          height_offset: float) -> Dict[str, int]:
        """
        Get summary of files for a specific session.
        
        Returns:
            Dictionary with file counts by mode and type
        """
        summary = {"OCC": {}, "GT": {}}
        
        try:
            for mode in ["OCC", "GT"]:
                for file_type in ["fbx", "png", "log"]:
                    output_dir = self.get_output_path(map_info, target, 
                                                    height_offset, mode, file_type)
                    if output_dir.exists():
                        count = len(list(output_dir.glob(f"*.{file_type}")))
                        summary[mode][file_type] = count
                    else:
                        summary[mode][file_type] = 0
                        
        except Exception as e:
            self.logger.error(f"Failed to generate session summary: {e}")
            
        return summary
        
    def _sanitize_name(self, name: str) -> str:
        """
        Sanitize name for use in filenames and directories.
        
        Args:
            name: Original name
            
        Returns:
            Sanitized name safe for filesystem use
        """
        # Remove or replace invalid characters
        invalid_chars = '<>:"/\\|?*'
        sanitized = name
        
        for char in invalid_chars:
            sanitized = sanitized.replace(char, "_")
            
        # Remove multiple underscores and trim
        while "__" in sanitized:
            sanitized = sanitized.replace("__", "_")
            
        return sanitized.strip("_")
        
    def create_completion_report(self, session_summaries: List[Dict]) -> Path:
        """
        Create a completion report with processing statistics.
        
        Args:
            session_summaries: List of session summary dictionaries
            
        Returns:
            Path to the generated report file
        """
        report_path = self.base_path / self.config.logs_subdir / "completion_report.txt"
        report_path.parent.mkdir(parents=True, exist_ok=True)
        
        try:
            with open(report_path, 'w') as f:
                f.write("Pipeline Automation Completion Report\n")
                f.write("=" * 50 + "\n")
                f.write(f"Generated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n\n")
                
                total_sessions = len(session_summaries)
                f.write(f"Total Sessions Processed: {total_sessions}\n\n")
                
                # Aggregate statistics
                total_files = {"OCC": {"fbx": 0, "png": 0, "log": 0},
                             "GT": {"fbx": 0, "png": 0, "log": 0}}
                
                for summary in session_summaries:
                    for mode in ["OCC", "GT"]:
                        for file_type in ["fbx", "png", "log"]:
                            total_files[mode][file_type] += summary.get(mode, {}).get(file_type, 0)
                
                f.write("File Generation Summary:\n")
                for mode in ["OCC", "GT"]:
                    f.write(f"  {mode} Mode:\n")
                    for file_type in ["fbx", "png", "log"]:
                        count = total_files[mode][file_type]
                        f.write(f"    {file_type.upper()} files: {count}\n")
                
                # Storage information
                sufficient, available_gb = self.check_storage_space()
                f.write(f"\nStorage Status:\n")
                f.write(f"  Available Space: {available_gb:.1f} GB\n")
                f.write(f"  Status: {'OK' if sufficient else 'LOW SPACE WARNING'}\n")
                
            self.logger.info(f"Completion report generated: {report_path}")
            return report_path
            
        except Exception as e:
            self.logger.error(f"Failed to create completion report: {e}")
            raise
    
    def initialize(self, **kwargs) -> bool:
        """
        Initialize the Output Manager (required by BaseManager).
        
        Returns:
            True if initialization successful
        """
        try:
            self._ensure_base_directory()
            self.logger.info("Output Manager initialized successfully")
            return True
        except Exception as e:
            self.logger.error(f"Failed to initialize Output Manager: {e}")
            return False
    
    def cleanup(self) -> None:
        """
        Cleanup Output Manager resources (required by BaseManager).
        """
        try:
            # Perform any necessary cleanup
            self.logger.info("Output Manager cleaned up")
        except Exception as e:
            self.logger.error(f"Error during Output Manager cleanup: {e}")