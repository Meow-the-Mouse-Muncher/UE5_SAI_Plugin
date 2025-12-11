"""
Base manager class for all pipeline managers.

This module provides the base functionality and interface that all
manager components should inherit from.
"""

import logging
from abc import ABC, abstractmethod
from typing import Optional, Dict, Any
from pathlib import Path

try:
    from ..models import ProcessingStatus
    from ..progress import ProgressTracker
except ImportError:
    import sys
    from pathlib import Path
    sys.path.append(str(Path(__file__).parent.parent))
    from models import ProcessingStatus
    from progress import ProgressTracker


class BaseManager(ABC):
    """Base class for all pipeline managers"""
    
    def __init__(self, progress_tracker: Optional[ProgressTracker] = None):
        """
        Initialize base manager
        
        Args:
            progress_tracker: Optional progress tracker for logging
        """
        self.progress_tracker = progress_tracker
        self.logger = logging.getLogger(self.__class__.__name__)
        self._retry_counts: Dict[str, int] = {}
        self._max_retries = 3
    
    def set_max_retries(self, max_retries: int) -> None:
        """Set maximum retry attempts for operations"""
        self._max_retries = max_retries
    
    def _reset_retry_count(self, operation_key: str) -> None:
        """Reset retry count for a specific operation"""
        if operation_key in self._retry_counts:
            del self._retry_counts[operation_key]
    
    def _increment_retry_count(self, operation_key: str) -> int:
        """
        Increment retry count for an operation
        
        Returns:
            Current retry count
        """
        self._retry_counts[operation_key] = self._retry_counts.get(operation_key, 0) + 1
        return self._retry_counts[operation_key]
    
    def _should_retry(self, operation_key: str) -> bool:
        """Check if operation should be retried"""
        return self._retry_counts.get(operation_key, 0) < self._max_retries
    
    def _log_retry(self, operation: str, attempt: int, error: str) -> None:
        """Log retry attempt"""
        self.logger.warning(f"Retry {attempt}/{self._max_retries} for {operation}: {error}")
        if self.progress_tracker:
            self.progress_tracker.record_retry(operation, attempt, self._max_retries)
    
    def _log_error(self, error_message: str, phase: Optional[str] = None) -> None:
        """Log error with progress tracking"""
        self.logger.error(error_message)
        if self.progress_tracker:
            self.progress_tracker.record_error(error_message, phase)
    
    def _log_info(self, message: str) -> None:
        """Log info message"""
        self.logger.info(message)
    
    def _log_warning(self, message: str) -> None:
        """Log warning message"""
        self.logger.warning(message)
    
    @abstractmethod
    def initialize(self, **kwargs) -> bool:
        """
        Initialize the manager
        
        Returns:
            True if initialization successful, False otherwise
        """
        pass
    
    @abstractmethod
    def cleanup(self) -> None:
        """Cleanup manager resources"""
        pass
    
    def get_status(self) -> Dict[str, Any]:
        """
        Get current status of the manager
        
        Returns:
            Dictionary with status information
        """
        return {
            "manager_type": self.__class__.__name__,
            "retry_counts": self._retry_counts.copy(),
            "max_retries": self._max_retries
        }