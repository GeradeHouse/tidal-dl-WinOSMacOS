#!/usr/bin/env python
# -*- encoding: utf-8 -*-
"""
GUI Logging Management System

This module provides centralized control for GUI-specific logging levels per module.
It allows fine-grained control over what messages appear in the GUI log area while
maintaining a unified logging system for console and file output.

Features:
- Per-module GUI log level configuration
- Thread-safe operations
- Backward compatibility with existing logger setup
- Easy migration from Printf system
"""

import logging
import threading
from typing import Dict, Optional


class GUILoggingManager:
    """
    Thread-safe manager for GUI-specific logging levels per module.
    
    This class maintains a mapping of module names to their GUI-specific log levels,
    allowing different modules to have different levels for GUI output while
    maintaining the same level for console/file output.
    """
    
    _instance = None
    _lock = threading.Lock()
    
    def __new__(cls):
        """Singleton pattern to ensure single instance across the application."""
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
                    cls._instance._initialize()
        return cls._instance
    
    def _initialize(self):
        """Initialize the GUI logging manager."""
        self._module_gui_levels: Dict[str, int] = {}
        self._default_level = logging.INFO
        self._gui_handler = None
        self._configured_modules = set()
    
    def set_gui_logger_level(self, module_name: str, level: int) -> None:
        """
        Set GUI-specific log level for a module.
        
        Args:
            module_name: The module name (usually __name__)
            level: The logging level (e.g., logging.INFO, logging.ERROR)
        """
        with self._lock:
            self._module_gui_levels[module_name] = level
            self._configured_modules.add(module_name)
            
        # Configure the module's logger if it exists
        try:
            logger = logging.getLogger(module_name)
            # The module's main logger level remains unchanged
            # Only GUI filtering is affected by this setting
        except Exception:
            # Logger might not be created yet, will be configured when logger is accessed
            pass
    
    def get_gui_level(self, module_name: str) -> int:
        """
        Get the GUI log level for a module.
        
        Args:
            module_name: The module name
            
        Returns:
            The log level for GUI output (default: INFO)
        """
        with self._lock:
            return self._module_gui_levels.get(module_name, self._default_level)
    
    def configure_gui_handler(self, gui_handler: logging.Handler) -> None:
        """
        Configure the GUI handler to use module-specific filtering.
        
        Args:
            gui_handler: The GUI log handler to configure
        """
        with self._lock:
            self._gui_handler = gui_handler
            
        # Add a custom filter to the GUI handler
        class ModuleLevelFilter(logging.Filter):
            def __init__(self, gui_manager: GUILoggingManager):
                super().__init__()
                self.gui_manager = gui_manager
            
            def filter(self, record: logging.LogRecord) -> bool:
                """Filter records based on module-specific GUI levels."""
                try:
                    # Get the module name from the record
                    module_name = record.name
                    
                    # Get the GUI level for this module
                    gui_level = self.gui_manager.get_gui_level(module_name)
                    
                    # Allow the record if its level meets the module's GUI level
                    return record.levelno >= gui_level
                    
                except Exception:
                    # If there's any error, allow the record to pass
                    return True
        
        # Add the module-level filter to the GUI handler
        gui_handler.addFilter(ModuleLevelFilter(self))
    
    def get_configured_modules(self) -> Dict[str, int]:
        """
        Get all configured module GUI levels.
        
        Returns:
            Dictionary mapping module names to their GUI levels
        """
        with self._lock:
            return self._module_gui_levels.copy()
    
    def get_unconfigured_modules(self) -> set:
        """
        Get modules that haven't been explicitly configured.
        
        Returns:
            Set of module names that use default GUI level
        """
        # This would need to be populated as modules are imported
        # For now, return empty set as we don't track unconfigured modules
        return set()


# Global manager instance
_gui_manager = None


def get_gui_manager() -> GUILoggingManager:
    """Get the global GUI logging manager instance."""
    global _gui_manager
    if _gui_manager is None:
        _gui_manager = GUILoggingManager()
    return _gui_manager


def setup_gui_logger(module_name: str, gui_level: int = logging.INFO) -> None:
    """
    Set up GUI-specific logging for a module.
    
    This function should be called at the top of each module that wants
    GUI-specific log level control.
    
    Args:
        module_name: Usually __name__ from the calling module
        gui_level: The level for GUI output (default: INFO)
    """
    manager = get_gui_manager()
    manager.set_gui_logger_level(module_name, gui_level)


def configure_module_logger(module_name: str, console_level: int = logging.DEBUG) -> logging.Logger:
    """
    Configure a standard module logger with GUI integration.
    
    Args:
        module_name: Usually __name__ from the calling module
        console_level: The level for console/file output (default: DEBUG)
        
    Returns:
        Configured logger instance
    """
    logger = logging.getLogger(module_name)
    logger.setLevel(console_level)
    return logger


# Backward compatibility functions
def get_logger(module_name: str) -> logging.Logger:
    """
    Get a logger configured for the module with GUI integration.
    
    Args:
        module_name: Usually __name__ from the calling module
        
    Returns:
        Logger instance for the module
    """
    return logging.getLogger(module_name)


def replace_printf_usage():
    """
    Helper function to identify Printf usage that needs to be migrated.
    This is a placeholder for migration analysis.
    """
    return [
        "logger.info() -> logger.info()",
        "Printf.success() -> logger.info()",
        "Printf.warning() -> logger.warning()",
        "logger.error() -> logger.error()"
    ]