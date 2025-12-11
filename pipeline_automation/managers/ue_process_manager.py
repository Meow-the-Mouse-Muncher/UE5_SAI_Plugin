"""
UE Process Manager component for the Pipeline Automation System.

This module extends the existing run_cmd_async.py functionality with enhanced
process management, crash detection, auto-restart, and socket communication.
"""

import asyncio
import socket
import subprocess
import sys
import time
import psutil
import logging
from pathlib import Path
from typing import Optional, Dict, Any, List, Callable
import json

try:
    from ..models import ProcessingStatus
    from .base_manager import BaseManager
except ImportError:
    import sys
    from pathlib import Path
    sys.path.append(str(Path(__file__).parent.parent))
    from models import ProcessingStatus
    from base_manager import BaseManager


class UEProcessManager(BaseManager):
    """
    Enhanced UE process management with crash recovery and communication.
    
    Extends run_cmd_async.py functionality with:
    - Process health monitoring and crash detection
    - Automatic restart and state recovery
    - Enhanced socket communication with command queuing
    - Process lifecycle management
    """
    
    def __init__(self, **kwargs):
        """Initialize UE Process Manager"""
        super().__init__(**kwargs)
        
        # Process management
        self.process: Optional[subprocess.Popen] = None
        self.process_command: Optional[str] = None
        self.is_ue_loaded = False
        self.restart_count = 0
        self.max_restarts = 5
        
        # Socket communication
        self.socket_host = "127.0.0.1"
        self.socket_port = 9999
        self.socket_server: Optional[socket.socket] = None
        self.client_socket: Optional[socket.socket] = None
        self.command_queue: List[str] = []
        
        # Monitoring
        self.health_check_interval = 10.0  # seconds
        self.crash_detection_enabled = True
        self.last_heartbeat = time.time()
        
        # Callbacks
        self.on_process_start: Optional[Callable] = None
        self.on_process_crash: Optional[Callable] = None
        self.on_ue_loaded: Optional[Callable] = None
    
    def initialize(self, socket_host: str = "127.0.0.1", socket_port: int = 9999,
                  max_restarts: int = 5) -> bool:
        """
        Initialize the UE Process Manager
        
        Args:
            socket_host: Host for socket communication
            socket_port: Port for socket communication  
            max_restarts: Maximum number of restart attempts
            
        Returns:
            True if initialization successful
        """
        try:
            self.socket_host = socket_host
            self.socket_port = socket_port
            self.max_restarts = max_restarts
            
            self._log_info(f"UE Process Manager initialized: {socket_host}:{socket_port}")
            return True
            
        except Exception as e:
            self._log_error(f"Failed to initialize UE Process Manager: {e}")
            return False
    
    async def start_ue_process(self, command: str, working_dir: Optional[Path] = None) -> bool:
        """
        Start UE process with monitoring
        
        Args:
            command: UE command to execute
            working_dir: Working directory for the process
            
        Returns:
            True if process started successfully
        """
        if self.process and self.process.poll() is None:
            self._log_warning("UE process already running")
            return True
        
        try:
            self.process_command = command
            self.is_ue_loaded = False
            
            self._log_info(f"Starting UE process: {command}")
            
            # Start process
            if working_dir:
                self.process = subprocess.Popen(
                    command, 
                    shell=True, 
                    cwd=str(working_dir),
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE
                )
            else:
                self.process = subprocess.Popen(command, shell=True)
            
            self._log_info(f"UE process started with PID: {self.process.pid}")
            
            # Start socket server
            await self._start_socket_server()
            
            # Start monitoring
            asyncio.create_task(self._monitor_process_health())
            
            if self.on_process_start:
                self.on_process_start()
            
            return True
            
        except Exception as e:
            self._log_error(f"Failed to start UE process: {e}")
            return False
    
    async def _start_socket_server(self) -> bool:
        """Start socket server for UE communication"""
        try:
            self.socket_server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            self.socket_server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            self.socket_server.bind((self.socket_host, self.socket_port))
            self.socket_server.listen(8)
            self.socket_server.setblocking(False)
            
            self._log_info(f"Socket server started on {self.socket_host}:{self.socket_port}")
            
            # Start accepting connections
            asyncio.create_task(self._handle_socket_connections())
            
            return True
            
        except Exception as e:
            self._log_error(f"Failed to start socket server: {e}")
            return False
    
    async def _handle_socket_connections(self) -> None:
        """Handle incoming socket connections"""
        loop = asyncio.get_event_loop()
        
        while self.socket_server:
            try:
                client, address = await loop.sock_accept(self.socket_server)
                self.client_socket = client
                self._log_info(f"Socket connection from {address}")
                
                # Handle client messages
                asyncio.create_task(self._handle_client_messages(client))
                
            except asyncio.CancelledError:
                break
            except Exception as e:
                self._log_warning(f"Socket connection error: {e}")
                await asyncio.sleep(1)
    
    async def _handle_client_messages(self, client: socket.socket) -> None:
        """Handle messages from UE client"""
        loop = asyncio.get_event_loop()
        
        while True:
            try:
                # Read message size (4 bytes)
                data_size = await loop.sock_recv(client, 4)
                if not data_size:
                    break
                
                size = int.from_bytes(data_size, byteorder='little')
                
                # Read message data
                data = await loop.sock_recv(client, size)
                message = data.decode('utf-8')
                
                await self._process_ue_message(message)
                
            except Exception as e:
                self._log_warning(f"Client message error: {e}")
                break
        
        client.close()
        if self.client_socket == client:
            self.client_socket = None
    
    async def _process_ue_message(self, message: str) -> None:
        """Process message from UE"""
        self.last_heartbeat = time.time()
        self._log_info(f"UE Message: {message}")
        
        # Handle special messages
        if message == '[*] Unreal Engine Loaded!':
            self.is_ue_loaded = True
            if self.on_ue_loaded:
                self.on_ue_loaded()
        
        elif 'error' in message.lower():
            self._log_error(f"UE Error: {message}")
            if self.crash_detection_enabled:
                await self._handle_process_crash("UE reported error")
        
        elif message in ['Render completed. Success: True', 'Pipeline Finished'] or 'exit' in message.lower():
            self._log_info("UE process completed normally")
            await self.stop_ue_process()
    
    async def send_command(self, command: str) -> bool:
        """
        Send command to UE process
        
        Args:
            command: Command to send
            
        Returns:
            True if command sent successfully
        """
        if not self.client_socket:
            self.command_queue.append(command)
            self._log_warning(f"No UE connection, queued command: {command}")
            return False
        
        try:
            message = command.encode('utf-8')
            size = len(message).to_bytes(4, byteorder='little')
            
            loop = asyncio.get_event_loop()
            await loop.sock_sendall(self.client_socket, size + message)
            
            self._log_info(f"Sent command to UE: {command}")
            return True
            
        except Exception as e:
            self._log_error(f"Failed to send command: {e}")
            return False
    
    async def _monitor_process_health(self) -> None:
        """Monitor UE process health and detect crashes"""
        while self.process:
            try:
                await asyncio.sleep(self.health_check_interval)
                
                if not self.process:
                    break
                
                # Check if process is still running
                poll_result = self.process.poll()
                if poll_result is not None:
                    self._log_error(f"UE process exited with code: {poll_result}")
                    await self._handle_process_crash(f"Process exit code: {poll_result}")
                    continue
                
                # Check for crash reporter
                if self._detect_crash_reporter():
                    self._log_error("Crash reporter detected")
                    await self._handle_process_crash("Crash reporter found")
                    continue
                
                # Check heartbeat timeout
                if self.is_ue_loaded and time.time() - self.last_heartbeat > 60:
                    self._log_warning("UE heartbeat timeout")
                    # Could trigger restart here if needed
                
            except asyncio.CancelledError:
                break
            except Exception as e:
                self._log_error(f"Health monitoring error: {e}")
    
    def _detect_crash_reporter(self) -> bool:
        """Detect if crash reporter is running"""
        try:
            for proc in psutil.process_iter(['pid', 'name']):
                if proc.info['name'] and 'CrashReportClient' in proc.info['name']:
                    # Kill crash reporter
                    try:
                        psutil.Process(proc.info['pid']).kill()
                        self._log_info(f"Killed crash reporter PID: {proc.info['pid']}")
                    except:
                        pass
                    return True
            return False
        except:
            return False
    
    async def _handle_process_crash(self, reason: str) -> None:
        """Handle UE process crash with restart logic"""
        self._log_error(f"UE process crash detected: {reason}")
        
        if self.on_process_crash:
            self.on_process_crash(reason)
        
        # Clean up current process
        await self._cleanup_process()
        
        # Check restart limit
        if self.restart_count >= self.max_restarts:
            self._log_error(f"Maximum restart attempts ({self.max_restarts}) exceeded")
            return
        
        # Restart process
        self.restart_count += 1
        self._log_info(f"Restarting UE process (attempt {self.restart_count}/{self.max_restarts})")
        
        await asyncio.sleep(5)  # Wait before restart
        
        if self.process_command:
            await self.start_ue_process(self.process_command)
    
    async def _cleanup_process(self) -> None:
        """Clean up process and socket resources"""
        # Kill process if still running
        if self.process:
            try:
                if self.process.poll() is None:
                    self.process.kill()
                    self.process.wait(timeout=10)
            except:
                pass
            self.process = None
        
        # Close socket connections
        if self.client_socket:
            try:
                self.client_socket.close()
            except:
                pass
            self.client_socket = None
        
        self.is_ue_loaded = False
    
    async def stop_ue_process(self) -> bool:
        """Stop UE process gracefully"""
        try:
            self._log_info("Stopping UE process")
            
            # Send exit command if connected
            if self.client_socket:
                await self.send_command("exit")
                await asyncio.sleep(2)  # Give time for graceful exit
            
            await self._cleanup_process()
            
            # Close socket server
            if self.socket_server:
                self.socket_server.close()
                self.socket_server = None
            
            self._log_info("UE process stopped")
            return True
            
        except Exception as e:
            self._log_error(f"Error stopping UE process: {e}")
            return False
    
    def is_process_responsive(self) -> bool:
        """Check if UE process is responsive"""
        if not self.process or self.process.poll() is not None:
            return False
        
        if not self.is_ue_loaded:
            return False
        
        # Check recent heartbeat
        return time.time() - self.last_heartbeat < 30
    
    async def wait_for_ue_ready(self, timeout: float = 120.0) -> bool:
        """
        Wait for UE to be ready for commands
        
        Args:
            timeout: Maximum time to wait in seconds
            
        Returns:
            True if UE is ready, False if timeout
        """
        start_time = time.time()
        
        while time.time() - start_time < timeout:
            if self.is_ue_loaded and self.client_socket:
                return True
            await asyncio.sleep(1)
        
        return False
    
    async def execute_queued_commands(self) -> int:
        """
        Execute any queued commands
        
        Returns:
            Number of commands executed
        """
        executed = 0
        
        while self.command_queue and self.client_socket:
            command = self.command_queue.pop(0)
            if await self.send_command(command):
                executed += 1
            else:
                # Put command back if failed
                self.command_queue.insert(0, command)
                break
        
        return executed
    
    def cleanup(self) -> None:
        """Cleanup UE Process Manager resources"""
        try:
            # This will be called synchronously, so we can't use await
            if self.process:
                try:
                    if self.process.poll() is None:
                        self.process.kill()
                except:
                    pass
            
            if self.socket_server:
                try:
                    self.socket_server.close()
                except:
                    pass
            
            if self.client_socket:
                try:
                    self.client_socket.close()
                except:
                    pass
        except:
            pass
        
        self._log_info("UE Process Manager cleaned up")
    
    def get_status(self) -> Dict[str, Any]:
        """Get current status of UE Process Manager"""
        base_status = super().get_status()
        
        process_status = {
            "process_running": self.process is not None and self.process.poll() is None,
            "process_pid": self.process.pid if self.process else None,
            "ue_loaded": self.is_ue_loaded,
            "socket_connected": self.client_socket is not None,
            "restart_count": self.restart_count,
            "max_restarts": self.max_restarts,
            "queued_commands": len(self.command_queue),
            "last_heartbeat": self.last_heartbeat,
            "is_responsive": self.is_process_responsive()
        }
        
        base_status.update(process_status)
        return base_status


# Utility functions
def create_ue_process_manager_from_config(pipeline_config) -> UEProcessManager:
    """Create UEProcessManager from pipeline configuration"""
    manager = UEProcessManager()
    manager.set_max_retries(pipeline_config.max_retry_attempts)
    
    success = manager.initialize(max_restarts=pipeline_config.max_retry_attempts)
    if not success:
        raise RuntimeError("Failed to initialize UE Process Manager")
    
    return manager