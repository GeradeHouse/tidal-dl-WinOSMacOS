#Requires -Version 5.1

<#
.SYNOPSIS
Builds the tidal-dl-gui executable using PyInstaller.

.DESCRIPTION
This script cleans up previous build artifacts and runs PyInstaller
to create a standalone executable for the Tidal Media Downloader GUI.
It allows specifying the build type (Windowed or Console).
Includes icons, fonts, and images assets. Excludes common ML libraries.
After a successful build, it also copies the resulting executable and the `_internal`
folder to a fixed test location so the app can be run and tested immediately.

.PARAMETER BuildType
Specifies the type of executable to build.
'Windowed' creates a standard GUI application without a console window (--noconsole).
'Console' creates an executable that opens a console window (useful for debugging).
Defaults to 'Windowed'.
(Kept for backward compatibility. Prefer using -Windowed or -Console.)

.PARAMETER Windowed
Switch-style way to request a windowed/GUI build. Equivalent to: -BuildType Windowed

.PARAMETER Console
Switch-style way to request a console build. Equivalent to: -BuildType Console

.EXAMPLE
.\build-tidal_dl_gui.ps1
Builds the default 'Windowed' executable (if no switches/parameters are supplied,
the script will prompt for a choice).

.EXAMPLE
.\build-tidal_dl_gui.ps1 -BuildType Console
Builds the 'Console' executable. (Legacy invocation, still supported.)

.EXAMPLE
.\build-tidal_dl_gui.ps1 -BuildType Windowed
Builds the 'Windowed' executable. (Legacy invocation, still supported.)

.EXAMPLE
.\build-tidal_dl_gui.ps1 -Windowed
Builds the 'Windowed' executable using the new switch-style invocation.

.EXAMPLE
.\build-tidal_dl_gui.ps1 -Console
Builds the 'Console' executable using the new switch-style invocation.
#>
param(
    [Parameter(Mandatory=$false)]
    [ValidateSet("Windowed", "Console")]
    [string]$BuildType,

    [Parameter(Mandatory=$false)]
    [switch]$Windowed,

    [Parameter(Mandatory=$false)]
    [switch]$Console
)

# --- Resolve Build Type (new switch style takes precedence) ---
if ($Windowed -and $Console) {
    Write-Error "You cannot specify both -Windowed and -Console at the same time."
    exit 1
}

if ($Windowed) {
    $BuildType = "Windowed"
} elseif ($Console) {
    $BuildType = "Console"
}

if (-not $BuildType) {
    Write-Host "Please select the build type:"
    $choices = @(
        [System.Management.Automation.Host.ChoiceDescription]::new("&Windowed", "Build a standard GUI application with no console.")
        [System.Management.Automation.Host.ChoiceDescription]::new("&Console", "Build a console application for debugging.")
    )
    $choice = $Host.UI.PromptForChoice("Build Type", "Select the build type:", $choices, 0)
    
    if ($choice -eq 0) {
        $BuildType = "Windowed"
    } else {
        $BuildType = "Console"
    }
}

# --- Define PyInstaller Path ---
$PyInstallerPath = Join-Path $PSScriptRoot ".venv\Scripts\pyinstaller.exe"

if (-not (Test-Path $PyInstallerPath)) {
    Write-Error "PyInstaller not found at $PyInstallerPath."
    Write-Error "Please ensure the virtual environment is set up and dependencies are installed by running:"
    Write-Error "pip install -r Tidal-Media-Downloader\TIDALDL-PY\requirements.txt"
    exit 1
}

# --- Check and Install AIGPY ---
Write-Host "Checking if AIGPY is installed..." -ForegroundColor Yellow
try {
    & ".venv\Scripts\python.exe" -c "import aigpy" 2>$null
    if ($LASTEXITCODE -eq 0) {
        Write-Host "AIGPY is already installed." -ForegroundColor Green
    } else {
        Write-Host "AIGPY not found. Installing AIGPY..." -ForegroundColor Yellow
        & ".venv\Scripts\pip.exe" install -e "AIGPY"
        if ($LASTEXITCODE -ne 0) {
            Write-Error "Failed to install AIGPY."
            exit 1
        }
        Write-Host "AIGPY installed successfully." -ForegroundColor Green
    }
} catch {
    Write-Error "Error checking/installing AIGPY: $($_.Exception.Message)"
    exit 1
}

# --- Check and Install Requirements ---
Write-Host "Checking if requirements are installed..." -ForegroundColor Yellow
$RequirementsPath = Join-Path $PSScriptRoot "Tidal-Media-Downloader\TIDALDL-PY\requirements.txt"
if (Test-Path $RequirementsPath) {
    try {
        & ".venv\Scripts\pip.exe" install -r $RequirementsPath
        if ($LASTEXITCODE -ne 0) {
            Write-Error "Failed to install requirements from $RequirementsPath."
            exit 1
        }
        Write-Host "Requirements installed successfully." -ForegroundColor Green
    } catch {
        Write-Error "Error installing requirements: $($_.Exception.Message)"
        exit 1
    }
} else {
    Write-Error "Requirements file not found at $RequirementsPath."
    exit 1
}

Write-Host "Using PyInstaller from: $PyInstallerPath" -ForegroundColor Green

# --- Configuration ---
$ScriptDir = $PSScriptRoot # Directory where this script is located
$ProjectSourceDir = Join-Path $ScriptDir "Tidal-Media-Downloader" # Path to the folder containing main.py
$PackageDir = Join-Path $ProjectSourceDir "TIDALDL-PY" # Path to the main Python package

# --- Build Variables ---
$AppName = "tidal-dl-gui"
$MainScript = "main.py" # Relative to $ProjectSourceDir
$IconFile = Join-Path $PackageDir "tidal_dl\assets\icons\icon-tidal-dl-gui.ico" # Relative to $PackageDir, use backslash for Windows path
$SplashImage = Join-Path $PackageDir "tidal_dl\assets\images\splash.png" # Path to the splash screen image

# --- Post-Build Copy Configuration ---
# The user requested that after building, the following path's artifacts are copied:
#   EXE:     <project-root>\Tidal-Media-Downloader\dist\tidal-dl-gui\tidal-dl-gui.exe
#   FOLDER:  <project-root>\Tidal-Media-Downloader\dist\tidal-dl-gui\_internal
# to:
#   C:\Users\imede.IME-DEKKER\OneDrive\Muziek\Tidal-dl-test
# We derive the source from the actual project root to keep it aligned with where we built.
$BuildOutputDir     = Join-Path $ProjectSourceDir ("dist\" + $AppName)
$PostBuildExePath   = Join-Path $BuildOutputDir ($AppName + ".exe")
$PostBuildFolder    = Join-Path $BuildOutputDir "_internal"
$PostBuildTargetDir = "C:\Users\imede.IME-DEKKER\OneDrive\Muziek\Tidal-dl-test"

# --- Start ---
Write-Host "-------------------------------------" -ForegroundColor Cyan
Write-Host "Starting Build for '$AppName'" -ForegroundColor Cyan
Write-Host "Build Type: $BuildType" -ForegroundColor Cyan
Write-Host "Project Source: $ProjectSourceDir" -ForegroundColor Cyan
Write-Host "-------------------------------------"

# --- Cleanup ---
Write-Host "Cleaning up previous build artifacts..." -ForegroundColor Yellow
# Define paths to remove directly
$DirectCleanupPaths = @(
    Join-Path $ProjectSourceDir "dist"              # Removed comma
    Join-Path $ProjectSourceDir "build"             # Removed comma
    Join-Path $ProjectSourceDir "$AppName.spec"     # Removed comma
    Join-Path $PackageDir "dist"                    # Removed comma
    Join-Path $PackageDir "build"                   # Removed comma
    Join-Path $PackageDir "MANIFEST.in"             # Removed comma
)

# Remove direct paths
foreach ($path in $DirectCleanupPaths) {
    if (Test-Path $path) {
        Write-Host "Removing: $path"
        Remove-Item -Path $path -Recurse -Force -ErrorAction SilentlyContinue
    }
}

# Find and remove .egg-info directories using Get-ChildItem
$EggInfoPaths = Get-ChildItem -Path $PackageDir -Directory -Filter "*.egg-info" -ErrorAction SilentlyContinue
foreach ($eggPath in $EggInfoPaths) {
    Write-Host "Removing: $($eggPath.FullName)"
    Remove-Item -Path $eggPath.FullName -Recurse -Force -ErrorAction SilentlyContinue
}

Write-Host "Cleanup complete." -ForegroundColor Green

# --- Set Working Directory ---
try {
    Set-Location -Path $ProjectSourceDir
    Write-Host "Changed working directory to: $(Get-Location)" -ForegroundColor Green
}
catch {
    Write-Error "Failed to change directory to '$ProjectSourceDir'. Please ensure the path is correct."
    exit 1
}

# --- Construct PyInstaller Command ---
# Base arguments
$pyinstallerArgs = @(
    # "--debug", "all", # Debug output PyiFrozenfinder logs ( Uncomment for debugging )
    "--noconfirm",    # Overwrite output directory without asking
    "-D",             # One-directory bundle
    $MainScript,
    "-n", $AppName,
    "-p", "TIDALDL-PY", # Add package directory to PyInstaller's path search
    "-p", "..\AIGPY",   # Add the directory containing the aigpy package
    "--exclude-module", "PyQt5",
    "--exclude-module", "PySide2",
    # --- ADDED EXCLUDES for ML Libraries ---
    "--exclude-module", "torch",
    "--exclude-module", "torchvision",
    "--exclude-module", "tensorflow",
    "--exclude-module", "tkinter",    # Exclude Tkinter
    "--exclude-module", "matplotlib", # Exclude Matplotlib
    # --- End Excludes ---
    "--hidden-import", "aigpy", # Explicitly include missing module
    "--icon=$IconFile",
    "--splash", $SplashImage, # Add the splash screen
    # Add data files (Syntax: SRC;DEST where SRC is relative to CWD, DEST is relative to bundle root)
    "--add-data", "TIDALDL-PY/tidal_dl/assets/icons;tidal_dl/assets/icons",
    "--add-data", "TIDALDL-PY/tidal_dl/assets/fonts;tidal_dl/assets/fonts",
    "--add-data", "TIDALDL-PY/tidal_dl/assets/images;tidal_dl/assets/images",
    "--add-data", "TIDALDL-PY/tidal_dl/metadata;tidal_dl/metadata" # <-- ADDED THIS LINE
)
# Add build type specific flag
if ($BuildType -eq "Windowed") {
    # --noconsole is preferred alias for --windowed on Windows
    $pyinstallerArgs += "--noconsole"
    Write-Host "Adding '--noconsole' flag for Windowed build."
} else { # This will be the "Console" case
    # No specific flag needed for console build, but you could add --console if you want to be explicit
    Write-Host "Building Console version (no '--noconsole' flag)."
}

# --- Execute PyInstaller ---
Write-Host "Running PyInstaller..." -ForegroundColor Yellow
Write-Host "Command: $PyInstallerPath $($pyinstallerArgs -join ' ')" # Show the command being run

try {
    # Use the call operator '&' with the full path and splatting '@' for the argument array
    & $PyInstallerPath @pyinstallerArgs
    # Check the exit code of the last command
    if ($LASTEXITCODE -ne 0) {
        Write-Error "PyInstaller failed with exit code $LASTEXITCODE."
        exit $LASTEXITCODE
    } else {
        Write-Host "PyInstaller build completed successfully!" -ForegroundColor Green
        Write-Host "Executable created in: $BuildOutputDir" -ForegroundColor Green
    }
}
catch {
    Write-Error "An error occurred during the PyInstaller execution: $($_.Exception.Message)"
    exit 1
}

# --- Restore Original Location (Optional) ---
Set-Location $ScriptDir
Write-Host "Restored working directory to: $ScriptDir"

# --- Post-Build: Copy Artifacts to Test Location ---
Write-Host "-------------------------------------" -ForegroundColor Cyan
Write-Host "Copying build artifacts to test location..." -ForegroundColor Cyan

# Ensure the target directory exists
try {
    if (-not (Test-Path $PostBuildTargetDir)) {
        Write-Host "Test directory does not exist. Creating: $PostBuildTargetDir" -ForegroundColor Yellow
        New-Item -ItemType Directory -Path $PostBuildTargetDir -Force | Out-Null
    }
} catch {
    Write-Error "Failed to ensure test directory '$PostBuildTargetDir' exists: $($_.Exception.Message)"
    # do not exit here; show that build finished but copy failed
}

# Copy the EXE
if (Test-Path $PostBuildExePath) {
    try {
        Write-Host "Copying EXE from '$PostBuildExePath' to '$PostBuildTargetDir'..." -ForegroundColor Yellow
        Copy-Item -Path $PostBuildExePath -Destination $PostBuildTargetDir -Force
        Write-Host "EXE copied successfully." -ForegroundColor Green
    } catch {
        Write-Error "Failed to copy EXE to test location: $($_.Exception.Message)"
    }
} else {
    Write-Warning "Expected EXE not found at '$PostBuildExePath'. Skipping EXE copy."
}

# Copy the _internal folder (and its contents)
if (Test-Path $PostBuildFolder) {
    try {
        Write-Host "Copying '_internal' folder from '$PostBuildFolder' to '$PostBuildTargetDir'..." -ForegroundColor Yellow
        Copy-Item -Path $PostBuildFolder -Destination $PostBuildTargetDir -Recurse -Force
        Write-Host "'_internal' folder copied successfully." -ForegroundColor Green
    } catch {
        Write-Error "Failed to copy '_internal' folder to test location: $($_.Exception.Message)"
    }
} else {
    Write-Warning "Expected '_internal' folder not found at '$PostBuildFolder'. Skipping folder copy."
}

Write-Host "-------------------------------------" -ForegroundColor Cyan
Write-Host "Build Script Finished." -ForegroundColor Cyan
Write-Host "Artifacts are available (where present) at: $PostBuildTargetDir" -ForegroundColor Cyan
Write-Host "-------------------------------------"
