#!/usr/bin/env python3
"""
Test Script for GUI Logging System
Demonstrates console vs GUI output separation with different logging levels.
"""

import sys
import logging
from pathlib import Path

# Add the tidal_dl module to Python path
sys.path.insert(0, str(Path(__file__).parent))

# Mock stdout for GUI simulation
class MockMainView:
    def __init__(self):
        self.log_messages = []
    
    def get_log_messages(self):
        return self.log_messages
    
    def simulate_stdout_write(self, message):
        """Simulate stdout being captured by GUI handler"""
        self.log_messages.append(f"GUI: {message.strip()}")

# Set up the mock environment
mock_main_view = MockMainView()

def setup_test_environment():
    """Set up test environment to simulate GUI logging"""
    # Import and set up GUI logging
    from tidal_dl.gui.gui_logging import setup_gui_logger, set_gui_main_view
    
    # Set up our mock main view
    set_gui_main_view(mock_main_view)
    
    # Simulate the logging setup that would happen in GUI
    setup_gui_logger("test_download", logging.INFO)
    setup_gui_logger("test_linking", logging.ERROR)
    setup_gui_logger("test_auth", logging.INFO)

def run_test_scenario():
    """Test different logging scenarios"""
    print("🧪 TESTING GUI LOGGING SYSTEM")
    print("=" * 50)
    
    # Test downloads.py-style logging (INFO level)
    logger_download = logging.getLogger("test_download")
    logger_download.info("[Download] Starting download of track...")
    logger_download.info("[Download] Converting to MP3...")
    logger_download.info("[Download] Download completed successfully!")
    
    # Test linking.py-style logging (ERROR level) 
    logger_linking = logging.getLogger("test_linking")
    logger_linking.info("[Linking] Starting job: Link tracks for playlist...")
    logger_linking.info("[Linking] Linking finished for row 1")
    logger_linking.error("[Linking] Failed to link track - no match found")
    
    # Test auth.py-style logging (INFO level)
    logger_auth = logging.getLogger("test_auth")  
    logger_auth.info("Starting authentication...")
    logger_auth.info("Login successful using stored credentials")
    logger_auth.warning("Token expired, refreshing...")
    logger_auth.info("Token refreshed successfully")
    
    # Mixed level test
    logger_download.debug("[Download] Debug: HTTP response status: 200")
    logger_download.warning("[Download] Warning: Slow download detected")

def show_results():
    """Display test results"""
    print("\n📊 TEST RESULTS")
    print("=" * 50)
    
    # Show what was sent to GUI (filtered)
    gui_messages = mock_main_view.get_log_messages()
    print(f"\n📱 GUI Log Messages ({len(gui_messages)} shown):")
    for i, msg in enumerate(gui_messages, 1):
        print(f"  {i}. {msg}")
    
    # Show what would go to console (all messages)
    print(f"\n💻 Console Log Messages (all levels):")
    print("  NOTE: All messages below would appear in console/terminal")
    print("  The GUI only shows INFO+ level messages from modules")
    
    print("\n✅ EXPECTED BEHAVIOR:")
    print("  - GUI shows: Download + Auth INFO messages")
    print("  - GUI hides: Linking INFO messages (ERROR level only)")
    print("  - Console shows: All messages regardless of level")
    print("  - Linking ERROR still visible in both GUI and Console")

def demonstrate_logging_levels():
    """Demonstrate logging level filtering"""
    print("\n🔍 LOGGING LEVEL DEMONSTRATION")
    print("=" * 50)
    
    print("Module Logging Levels:")
    print("  test_download: INFO (shows INFO, WARNING, ERROR)")
    print("  test_linking:  ERROR (shows only ERROR)")  
    print("  test_auth:     INFO (shows INFO, WARNING, ERROR)")
    
    print("\nMessages by Level:")
    print("  DEBUG (test_download): 'Debug: HTTP response' - FILTERED from GUI")
    print("  INFO (download/auth):   'Download completed' - SHOWN in GUI")
    print("  WARNING (all):         'Slow download' - SHOWN in GUI") 
    print("  ERROR (linking):       'Failed to link' - SHOWN in GUI")

if __name__ == "__main__":
    setup_test_environment()
    run_test_scenario()
    show_results()
    demonstrate_logging_levels()
    
    print("\n🎉 TEST COMPLETE!")
    print("This demonstrates how the GUI logging system separates:")
    print("1. Console: All messages (DEBUG +)")
    print("2. GUI: Only INFO+ from modules with INFO+ level")
    print("3. Level Control: Each module has independent GUI level")