"""
Progress tracking and logging system for the Pipeline Automation System.

This module provides comprehensive progress tracking, time estimation, 
state persistence, and logging functionality for the pipeline execution.
"""

import json
import time
import logging
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Dict, List, Optional, Any, Union
from datetime import datetime, timedelta
import threading
from collections import deque

try:
    from .models import ProcessingStatus, MapInfo, TargetObject, SamplingSession
except ImportError:
    from models import ProcessingStatus, MapInfo, TargetObject, SamplingSession


@dataclass
class ProgressStatus:
    """Comprehensive progress status for the pipeline"""
    # Current processing state
    current_map: str = ""
    current_target: str = ""
    current_height: float = 0.0
    current_trajectory: str = ""
    current_phase: str = ""  # map_loading, target_discovery, trajectory_execution, etc.
    
    # Progress counters
    maps_completed: int = 0
    total_maps: int = 0
    targets_completed: int = 0
    total_targets: int = 0
    sessions_completed: int = 0
    total_sessions: int = 0
    
    # Time tracking
    pipeline_start_time: Optional[float] = None
    current_phase_start_time: Optional[float] = None
    estimated_time_remaining: str = "Calculating..."
    elapsed_time: str = "00:00:00"
    
    # Performance metrics
    average_session_time: float = 0.0
    sessions_per_hour: float = 0.0
    
    # Error tracking
    total_errors: int = 0
    total_retries: int = 0
    
    def __post_init__(self):
        """Initialize start time if not set"""
        if self.pipeline_start_time is None:
            self.pipeline_start_time = time.time()
    
    @property
    def overall_progress_percentage(self) -> float:
        """Calculate overall progress as percentage"""
        if self.total_sessions == 0:
            return 0.0
        return (self.sessions_completed / self.total_sessions) * 100.0
    
    @property
    def map_progress_percentage(self) -> float:
        """Calculate map progress as percentage"""
        if self.total_maps == 0:
            return 0.0
        return (self.maps_completed / self.total_maps) * 100.0
    
    @property
    def target_progress_percentage(self) -> float:
        """Calculate target progress as percentage"""
        if self.total_targets == 0:
            return 0.0
        return (self.targets_completed / self.total_targets) * 100.0
    
    def update_elapsed_time(self) -> None:
        """Update elapsed time string"""
        if self.pipeline_start_time:
            elapsed_seconds = time.time() - self.pipeline_start_time
            self.elapsed_time = self._format_duration(elapsed_seconds)
    
    def _format_duration(self, seconds: float) -> str:
        """Format duration in seconds to HH:MM:SS format"""
        hours = int(seconds // 3600)
        minutes = int((seconds % 3600) // 60)
        seconds = int(seconds % 60)
        return f"{hours:02d}:{minutes:02d}:{seconds:02d}"


class ProgressTracker:
    """Advanced progress tracking with time estimation and persistence"""
    
    def __init__(self, state_file: Optional[Path] = None, log_interval: int = 30):
        """
        Initialize progress tracker
        
        Args:
            state_file: Path to save/load progress state
            log_interval: Interval in seconds for automatic progress logging
        """
        self.state_file = Path(state_file) if state_file else None
        self.log_interval = log_interval
        
        self.status = ProgressStatus()
        self._session_times: deque = deque(maxlen=50)  # Keep last 50 session times
        self._phase_times: Dict[str, deque] = {}  # Track times per phase
        self._lock = threading.Lock()
        self._last_log_time = time.time()
        
        # Load existing state if available
        if self.state_file and self.state_file.exists():
            self.load_state()
    
    def initialize_pipeline(self, total_maps: int, total_targets: int, total_sessions: int) -> None:
        """
        Initialize pipeline with total counts
        
        Args:
            total_maps: Total number of maps to process
            total_targets: Total number of targets across all maps
            total_sessions: Total number of sampling sessions
        """
        with self._lock:
            self.status.total_maps = total_maps
            self.status.total_targets = total_targets
            self.status.total_sessions = total_sessions
            self.status.pipeline_start_time = time.time()
            
        logging.info(f"Pipeline initialized: {total_maps} maps, {total_targets} targets, {total_sessions} sessions")
        self._save_state()
    
    def start_map_processing(self, map_name: str, target_count: int) -> None:
        """
        Start processing a new map
        
        Args:
            map_name: Name of the map being processed
            target_count: Number of targets in this map
        """
        with self._lock:
            self.status.current_map = map_name
            self.status.current_phase = "map_loading"
            self.status.current_phase_start_time = time.time()
            
        logging.info(f"Starting map processing: {map_name} ({target_count} targets)")
        self._log_progress_if_needed()
    
    def complete_map_processing(self, map_name: str) -> None:
        """
        Complete processing of a map
        
        Args:
            map_name: Name of the completed map
        """
        with self._lock:
            self.status.maps_completed += 1
            
        logging.info(f"Completed map processing: {map_name}")
        self._save_state()
    
    def start_target_processing(self, target_name: str) -> None:
        """
        Start processing a new target
        
        Args:
            target_name: Name of the target being processed
        """
        with self._lock:
            self.status.current_target = target_name
            self.status.current_phase = "target_processing"
            self.status.current_phase_start_time = time.time()
            
        logging.info(f"Starting target processing: {target_name}")
    
    def complete_target_processing(self, target_name: str) -> None:
        """
        Complete processing of a target
        
        Args:
            target_name: Name of the completed target
        """
        with self._lock:
            self.status.targets_completed += 1
            
        logging.info(f"Completed target processing: {target_name}")
    
    def start_session(self, map_name: str, target_name: str, height: float, trajectory_name: str) -> None:
        """
        Start a new sampling session
        
        Args:
            map_name: Map name
            target_name: Target name
            height: Height offset
            trajectory_name: Trajectory name
        """
        with self._lock:
            self.status.current_map = map_name
            self.status.current_target = target_name
            self.status.current_height = height
            self.status.current_trajectory = trajectory_name
            self.status.current_phase = "trajectory_execution"
            self.status.current_phase_start_time = time.time()
            
        logging.info(f"Starting session: {map_name}/{target_name}/h{height}/{trajectory_name}")
    
    def complete_session(self, session: SamplingSession, success: bool = True) -> None:
        """
        Complete a sampling session
        
        Args:
            session: Completed sampling session
            success: Whether the session was successful
        """
        session_duration = session.session_duration
        
        with self._lock:
            self.status.sessions_completed += 1
            
            # Update session timing statistics
            if session_duration > 0:
                self._session_times.append(session_duration)
                self._update_time_estimates()
            
        if success:
            logging.info(f"Session completed successfully in {session_duration:.1f}s: {session.map_name}/{session.target_name}")
        else:
            logging.warning(f"Session failed after {session_duration:.1f}s: {session.map_name}/{session.target_name}")
            
        self._save_state()
        self._log_progress_if_needed()
    
    def record_error(self, error_message: str, phase: Optional[str] = None) -> None:
        """
        Record an error occurrence
        
        Args:
            error_message: Description of the error
            phase: Phase where error occurred
        """
        with self._lock:
            self.status.total_errors += 1
            
        phase_info = f" during {phase}" if phase else ""
        logging.error(f"Error recorded{phase_info}: {error_message}")
        self._save_state()
    
    def record_retry(self, operation: str, attempt: int, max_attempts: int) -> None:
        """
        Record a retry attempt
        
        Args:
            operation: Description of operation being retried
            attempt: Current attempt number
            max_attempts: Maximum number of attempts
        """
        with self._lock:
            self.status.total_retries += 1
            
        logging.warning(f"Retry {attempt}/{max_attempts} for {operation}")
    
    def _update_time_estimates(self) -> None:
        """Update time estimates based on historical data"""
        if not self._session_times:
            return
            
        # Calculate average session time
        self.status.average_session_time = sum(self._session_times) / len(self._session_times)
        
        # Calculate sessions per hour
        if self.status.average_session_time > 0:
            self.status.sessions_per_hour = 3600.0 / self.status.average_session_time
        
        # Estimate remaining time
        remaining_sessions = self.status.total_sessions - self.status.sessions_completed
        if remaining_sessions > 0 and self.status.average_session_time > 0:
            estimated_seconds = remaining_sessions * self.status.average_session_time
            self.status.estimated_time_remaining = self.status._format_duration(estimated_seconds)
        else:
            self.status.estimated_time_remaining = "00:00:00"
    
    def _log_progress_if_needed(self) -> None:
        """Log progress if enough time has passed since last log"""
        current_time = time.time()
        if current_time - self._last_log_time >= self.log_interval:
            self.log_progress()
            self._last_log_time = current_time
    
    def log_progress(self) -> None:
        """Log current progress status"""
        self.status.update_elapsed_time()
        
        progress_msg = (
            f"Progress: {self.status.overall_progress_percentage:.1f}% "
            f"({self.status.sessions_completed}/{self.status.total_sessions} sessions) | "
            f"Maps: {self.status.maps_completed}/{self.status.total_maps} | "
            f"Targets: {self.status.targets_completed}/{self.status.total_targets} | "
            f"Elapsed: {self.status.elapsed_time} | "
            f"ETA: {self.status.estimated_time_remaining}"
        )
        
        if self.status.current_phase:
            progress_msg += f" | Current: {self.status.current_phase}"
            
        if self.status.current_map:
            progress_msg += f" | Map: {self.status.current_map}"
            
        if self.status.current_target:
            progress_msg += f" | Target: {self.status.current_target}"
        
        logging.info(progress_msg)
        
        # Log performance metrics if available
        if self.status.sessions_per_hour > 0:
            perf_msg = (
                f"Performance: {self.status.sessions_per_hour:.1f} sessions/hour | "
                f"Avg session time: {self.status.average_session_time:.1f}s | "
                f"Errors: {self.status.total_errors} | Retries: {self.status.total_retries}"
            )
            logging.info(perf_msg)
    
    def get_detailed_status(self) -> Dict[str, Any]:
        """
        Get detailed status information as dictionary
        
        Returns:
            Dictionary with comprehensive status information
        """
        self.status.update_elapsed_time()
        
        with self._lock:
            status_dict = asdict(self.status)
            
        # Add additional computed metrics
        status_dict.update({
            'overall_progress_percentage': self.status.overall_progress_percentage,
            'map_progress_percentage': self.status.map_progress_percentage,
            'target_progress_percentage': self.status.target_progress_percentage,
            'recent_session_times': list(self._session_times),
            'timestamp': datetime.now().isoformat()
        })
        
        return status_dict
    
    def save_state(self) -> None:
        """Manually save current state to file"""
        self._save_state()
    
    def _save_state(self) -> None:
        """Internal method to save state to file"""
        if not self.state_file:
            return
            
        try:
            self.state_file.parent.mkdir(parents=True, exist_ok=True)
            
            state_data = self.get_detailed_status()
            
            with open(self.state_file, 'w') as f:
                json.dump(state_data, f, indent=2)
                
        except Exception as e:
            logging.error(f"Failed to save progress state: {e}")
    
    def load_state(self) -> bool:
        """
        Load progress state from file
        
        Returns:
            True if state was loaded successfully, False otherwise
        """
        if not self.state_file or not self.state_file.exists():
            return False
            
        try:
            with open(self.state_file, 'r') as f:
                state_data = json.load(f)
            
            # Restore status fields
            for field_name, field_value in state_data.items():
                if hasattr(self.status, field_name):
                    setattr(self.status, field_name, field_value)
            
            # Restore session times if available
            if 'recent_session_times' in state_data:
                self._session_times.extend(state_data['recent_session_times'])
            
            logging.info(f"Progress state loaded from {self.state_file}")
            return True
            
        except Exception as e:
            logging.error(f"Failed to load progress state: {e}")
            return False
    
    def generate_summary_report(self) -> str:
        """
        Generate a comprehensive summary report
        
        Returns:
            Formatted summary report string
        """
        self.status.update_elapsed_time()
        
        report_lines = [
            "=" * 60,
            "PIPELINE EXECUTION SUMMARY",
            "=" * 60,
            f"Total Execution Time: {self.status.elapsed_time}",
            f"Overall Progress: {self.status.overall_progress_percentage:.1f}%",
            "",
            "PROCESSING STATISTICS:",
            f"  Maps: {self.status.maps_completed}/{self.status.total_maps} completed",
            f"  Targets: {self.status.targets_completed}/{self.status.total_targets} completed", 
            f"  Sessions: {self.status.sessions_completed}/{self.status.total_sessions} completed",
            "",
            "PERFORMANCE METRICS:",
            f"  Average Session Time: {self.status.average_session_time:.1f} seconds",
            f"  Sessions per Hour: {self.status.sessions_per_hour:.1f}",
            f"  Total Errors: {self.status.total_errors}",
            f"  Total Retries: {self.status.total_retries}",
            ""
        ]
        
        if self.status.sessions_completed < self.status.total_sessions:
            report_lines.extend([
                "REMAINING WORK:",
                f"  Estimated Time Remaining: {self.status.estimated_time_remaining}",
                f"  Sessions Remaining: {self.status.total_sessions - self.status.sessions_completed}",
                ""
            ])
        
        report_lines.append("=" * 60)
        
        return "\n".join(report_lines)


class PipelineLogger:
    """Enhanced logging system for the pipeline with structured output"""
    
    def __init__(self, log_dir: Path, log_level: int = logging.INFO):
        """
        Initialize pipeline logger
        
        Args:
            log_dir: Directory for log files
            log_level: Logging level
        """
        self.log_dir = Path(log_dir)
        self.log_dir.mkdir(parents=True, exist_ok=True)
        
        # Create timestamp for this run
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        
        # Setup main pipeline log
        self.main_log_file = self.log_dir / f"pipeline_{timestamp}.log"
        self.error_log_file = self.log_dir / f"errors_{timestamp}.log"
        
        # Configure logging
        self._setup_logging(log_level)
        
        logging.info(f"Pipeline logging initialized - Main: {self.main_log_file}")
    
    def _setup_logging(self, log_level: int) -> None:
        """Setup logging configuration"""
        # Create formatters
        detailed_formatter = logging.Formatter(
            '[%(asctime)s] %(levelname)-8s [%(name)s] %(message)s',
            datefmt='%Y-%m-%d %H:%M:%S'
        )
        
        simple_formatter = logging.Formatter(
            '[%(asctime)s] %(levelname)-8s %(message)s',
            datefmt='%H:%M:%S'
        )
        
        # Setup root logger
        root_logger = logging.getLogger()
        root_logger.setLevel(log_level)
        
        # Clear existing handlers
        root_logger.handlers.clear()
        
        # Console handler
        console_handler = logging.StreamHandler()
        console_handler.setLevel(log_level)
        console_handler.setFormatter(simple_formatter)
        root_logger.addHandler(console_handler)
        
        # Main log file handler
        main_file_handler = logging.FileHandler(self.main_log_file)
        main_file_handler.setLevel(log_level)
        main_file_handler.setFormatter(detailed_formatter)
        root_logger.addHandler(main_file_handler)
        
        # Error log file handler (errors and warnings only)
        error_file_handler = logging.FileHandler(self.error_log_file)
        error_file_handler.setLevel(logging.WARNING)
        error_file_handler.setFormatter(detailed_formatter)
        root_logger.addHandler(error_file_handler)
    
    def log_pipeline_start(self, config_summary: Dict[str, Any]) -> None:
        """Log pipeline start with configuration summary"""
        logging.info("=" * 60)
        logging.info("PIPELINE AUTOMATION SYSTEM STARTED")
        logging.info("=" * 60)
        
        for key, value in config_summary.items():
            logging.info(f"Config - {key}: {value}")
        
        logging.info("=" * 60)
    
    def log_pipeline_complete(self, summary_report: str) -> None:
        """Log pipeline completion with summary"""
        logging.info("PIPELINE EXECUTION COMPLETED")
        logging.info(summary_report)
    
    def get_log_files(self) -> Dict[str, Path]:
        """Get paths to all log files"""
        return {
            'main_log': self.main_log_file,
            'error_log': self.error_log_file
        }