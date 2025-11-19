#!/usr/bin/env python
print("[DEBUG] Importing in main.py")
import os
import sys
print("[DEBUG] Finished importing os, sys in main.py")

# --- START: PYINSTALLER RUNTIME DEBUGGING ---
def print_pyinstaller_debug_info():
    print("\n--- PYINSTALLER RUNTIME DEBUG ---")
    print(f"Python Executable: {sys.executable}")
    print(f"sys.frozen: {getattr(sys, 'frozen', 'Not frozen')}")
    
    # In a PyInstaller bundle, _MEIPASS is the temp directory where files are unpacked
    meipass = getattr(sys, '_MEIPASS', None)
    if meipass:
        print(f"sys._MEIPASS (Bundle Root): {meipass}")
        print("--- Contents of _MEIPASS/tidal_dl/gui ---")
        gui_path = os.path.join(meipass, 'tidal_dl', 'gui')
        if os.path.exists(gui_path):
            try:
                # List all files in the bundled gui directory
                for filename in sorted(os.listdir(gui_path)):
                    print(f"  - {filename}")
            except Exception as e:
                print(f"  - Could not list directory contents: {e}")
        else:
            print("  - tidal_dl/gui directory NOT FOUND in bundle.")
    
    print("--- sys.path ---")
    for p in sys.path:
        print(f"  - {p}")
    
    print("--- Manual Import Test ---")
    try:
        # Attempt to import the problematic module directly
        import tidal_dl.gui.gui_playlist_item_widget
        print("  - SUCCESS: Manually imported tidal_dl.gui.gui_playlist_item_widget")
    except ImportError as e:
        print(f"  - FAILED to manually import: {e}")
    except Exception as e:
        print(f"  - FAILED with unexpected error: {e}")
    print("--- END PYINSTALLER RUNTIME DEBUG ---\n")
# --- END: PYINSTALLER RUNTIME DEBUGGING ---


# --- START: Add FFmpeg Configuration for MoviePy ---
# This must be done before any part of the app that might use moviepy.
try:
    import imageio_ffmpeg
    import moviepy.config
    
    # Get the path to the ffmpeg executable from imageio_ffmpeg
    ffmpeg_exe = imageio_ffmpeg.get_ffmpeg_exe()
    
    # Set the FFMPEG_BINARY configuration for moviepy
    moviepy.config.FFMPEG_BINARY = ffmpeg_exe
    
    print(f"[DEBUG] MoviePy's FFMPEG_BINARY set to: {ffmpeg_exe}")
except ImportError:
    print("[WARNING] imageio_ffmpeg or moviepy not found. Conversion features may fail.")
except Exception as e:
    print(f"[ERROR] Failed to configure FFmpeg for MoviePy: {e}")
# --- END: Add FFmpeg Configuration ---

# --- Splash Screen Integration ---
# This must be done before other imports that might take time.
try:
    import pyi_splash # type: ignore
    # Update the splash screen with an initial message
    pyi_splash.update_text("Starting up...")
except ImportError:
    # This will fail when not running from a PyInstaller bundle, which is fine.
    pyi_splash = None
# --- End Splash Screen Integration ---

def main():
    # Call the debug info function at the very start
    print_pyinstaller_debug_info()

    # Add package path for tidal_dl so it can be imported correctly.
    base_dir = os.path.dirname(os.path.abspath(__file__))
    package_path = os.path.join(base_dir, "TIDALDL-PY")
    if package_path not in sys.path:
        sys.path.insert(0, package_path)

    # Update splash screen progress before heavy imports
    if pyi_splash:
        pyi_splash.update_text("Loading core components...")

    try:
        print("[DEBUG] About to import tidal_dl.login")
        from tidal_dl.login import initialize_and_login
        print("[DEBUG] Successfully imported initialize_and_login")
        
        print("[DEBUG] About to import tidal_dl.gui")
        from tidal_dl.gui import main as gui_main
        print("[DEBUG] Successfully imported gui_main")
    except ImportError as e:
        print(f"Error: A critical component failed to import: {e}")
        print(f"[DEBUG] Import error details: {type(e).__name__}: {str(e)}")
        print(f"[DEBUG] Import error module: {getattr(e, 'name', 'Unknown')}")
        print(f"[DEBUG] Import error path: {getattr(e, 'path', 'Unknown')}")
        import traceback
        print(f"[DEBUG] Full traceback:")
        traceback.print_exc()
        # Add a pause so the user can see the error in a bundled app
        input("Press Enter to exit...")
        sys.exit(1)
    except Exception as e:
        print(f"Error: Unexpected error during import: {e}")
        print(f"[DEBUG] Error details: {type(e).__name__}: {str(e)}")
        import traceback
        print(f"[DEBUG] Full traceback:")
        traceback.print_exc()
        # Add a pause so the user can see the error in a bundled app
        input("Press Enter to exit...")
        sys.exit(1)

    # Update splash screen progress before initialization
    if pyi_splash:
        pyi_splash.update_text("Initializing settings...")

    # The initialization and login logic is now called from within the GUI startup process
    # in gui_app_setup.py to ensure the API key is set before the GUI tries to use it.
    # We no longer call initialize_and_login() here directly.

    # Force GUI mode by manipulating sys.argv before calling the GUI's main function
    sys.argv = ["tidal-dl", "--gui"]
    
    # Update splash screen just before launching the GUI
    if pyi_splash:
        pyi_splash.update_text("Launching GUI...")

    # --- IMPORTANT: Close the splash screen ---
    # This must be called right before the application's main loop starts.
    if pyi_splash:
        pyi_splash.close()

    # Start the GUI application
    return gui_main()

if __name__ == '__main__':
    # PyInstaller creates a temp folder and packs your app in it.
    # This check is needed to prevent the app from re-launching itself
    # when a child process is spawned (e.g., by moviepy).
    import multiprocessing
    multiprocessing.freeze_support()
    
    sys.exit(main())