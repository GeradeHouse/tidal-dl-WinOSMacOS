#!/usr/bin/env python
print("[DEBUG] Importing in main.py")
import os
import sys
print("[DEBUG] Finished importing os, sys in main.py")

def main():
    # Add package path for tidal_dl so it can be imported correctly.
    base_dir = os.path.dirname(os.path.abspath(__file__))
    package_path = os.path.join(base_dir, "TIDALDL-PY")
    if package_path not in sys.path:
        sys.path.insert(0, package_path)

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

    # Attempt to initialize settings and log in.
    # The GUI will handle the login flow if this fails.
    initialize_and_login()

    # Force GUI mode by manipulating sys.argv before calling the GUI's main function
    sys.argv = ["tidal-dl", "--gui"]
    
    # Start the GUI application
    return gui_main()

if __name__ == '__main__':
    sys.exit(main())