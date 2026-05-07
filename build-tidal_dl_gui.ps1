#Requires -Version 5.1

<#
.SYNOPSIS
Builds the Tidal-DL GUI executable and optionally creates an Installer.

.DESCRIPTION
This script automates the build process for the Tidal Media Downloader GUI.
It performs the following steps:
1.  Increments the version number in 'version.iss' (x.y.9 -> x.y+1.0).
2.  Cleans up previous build artifacts.
3.  Runs PyInstaller to create a standalone executable (Windowed or Console).
4.  (Optional) Copies artifacts to a specific OneDrive test folder if -Testing is used.
5.  (Optional) Compiles an Installer using Inno Setup if -Installer is used.

.PARAMETER Windowed
Builds a standard GUI application (no console window). 
This is the default behavior if no build type is specified.
Example: .\build-tidal_dl_gui.ps1 -Windowed

.PARAMETER Console
Builds a console application. 
Useful for debugging crashes or viewing live logs in a command prompt.
Example: .\build-tidal_dl_gui.ps1 -Console

.PARAMETER Installer
If specified, runs Inno Setup (iscc) after the build to create the installer executable.
The installer is placed in the 'dist-installer' folder.
Example: .\build-tidal_dl_gui.ps1 -Windowed -Installer

.PARAMETER Testing
Enables "Testing Mode". Behavior depends on whether -Installer is used:
1.  If -Installer is NOT used: Copies the built EXE and '_internal' folder directly to:
    'C:\Users\imede.IME-DEKKER\OneDrive\Muziek\Tidal-dl-test'
2.  If -Installer IS used: Skips the direct file copy. Instead, compiles the installer
    with the 'TestingMode' flag, causing it to install into 'Music\Tidal-dl-test'.
Example: .\build-tidal_dl_gui.ps1 -Windowed -Testing

.PARAMETER DeployInstalled
Enables "Installed Deploy Mode" for fast local iteration without rebuilding the installer.
After PyInstaller completes, the script copies the rebuilt EXE and replaces the '_internal'
folder in the actual installed OneDrive location:

    C:\Users\imede.IME-DEKKER\OneDrive\Muziek\Tidal-dl

Before copying, the script checks whether tidal-dl-gui.exe is running and force-closes it
to avoid locked EXE/DLL files. The deployment only replaces the EXE and '_internal' folder;
other existing files and settings in the install folder are left untouched.

This flag cannot be combined with -Installer or -Testing.

Example: .\build-tidal_dl_gui.ps1 -Windowed -BuildMode Fast -DeployInstalled

.PARAMETER BuildMode
Controls the cleanup strategy:
- 'Full' (Default): Deletes all artifacts (dist, build, spec) and reinstalls requirements.
- 'Fast': Keeps the 'build' cache and skips pip install. Use this for quick code iterations.
Example: .\build-tidal_dl_gui.ps1 -BuildMode Fast

.PARAMETER BuildType
Legacy parameter for specifying 'Windowed' or 'Console'. 
Prefer using the -Windowed or -Console switches above.

.PARAMETER Help
Displays this help documentation.
Alias: -h

.EXAMPLE
# 1. Standard Build (GUI only, no installer)
.\build-tidal_dl_gui.ps1 -Windowed

.EXAMPLE
# 2. Create a Standard Installer
# Builds the GUI, increments version, and creates an installer in 'dist-installer'.
.\build-tidal_dl_gui.ps1 -Windowed -Installer

.EXAMPLE
# 3. Create a Test Build & Installer
# Builds GUI, skips manual copy, and creates an installer that installs to 'Music\Tidal-dl-test'.
.\build-tidal_dl_gui.ps1 -Windowed -Installer -Testing

.EXAMPLE
# 4. Test Raw Executable (No Installer)
# Builds GUI and copies files directly to 'Music\Tidal-dl-test' for immediate testing.
.\build-tidal_dl_gui.ps1 -Windowed -Testing

.EXAMPLE
# 5. Fast Iteration
# Rebuilds the EXE without reinstalling dependencies or clearing the build cache.
.\build-tidal_dl_gui.ps1 -Windowed -BuildMode Fast

.EXAMPLE
# 6. Show Help
.\build-tidal_dl_gui.ps1 -Help
.\build-tidal_dl_gui.ps1 -h

.EXAMPLE
# 7. Fast Deploy to Actual Installed Location
# Rebuilds the EXE, closes the running app if needed, then deploys to the real install folder.
.\build-tidal_dl_gui.ps1 -Windowed -BuildMode Fast -DeployInstalled
#>
param(
    [Parameter(Mandatory=$false)]
    [ValidateSet("Windowed", "Console")]
    [string]$BuildType,

    [Parameter(Mandatory=$false)]
    [switch]$Windowed,

    [Parameter(Mandatory=$false)]
    [switch]$Console,

    [Parameter(Mandatory=$false)]
    [ValidateSet("Full", "Fast")]
    [string]$BuildMode = "Full",

    [Parameter(Mandatory=$false)]
    [switch]$Installer,

    [Parameter(Mandatory=$false)]
    [switch]$Testing,

    [Parameter(Mandatory=$false)]
    [switch]$DeployInstalled,

    [Parameter(Mandatory=$false)]
    [Alias("h")]
    [switch]$Help
)

# --- Help Check ---
if ($Help) {
    Get-Help $PSCommandPath -Full
    exit 0
}

# --- Configuration ---
$ScriptDir = $PSScriptRoot # Directory where this script is located
$ProjectSourceDir = Join-Path $ScriptDir "Tidal-Media-Downloader" # Path to the folder containing main.py
$PackageDir = Join-Path $ProjectSourceDir "TIDALDL-PY" # Path to the main Python package

# --- 0. Automatic Version Increment ---
# This runs every time the script is executed.
# Targets the separate version file 'version.iss' to avoid Git noise on the main script.
$IssPath = Join-Path $ScriptDir "version.iss"
if (Test-Path $IssPath) {
    Write-Host "Checking version in version.iss..." -ForegroundColor Yellow
    try {
        $issContent = Get-Content -Path $IssPath -Raw -Encoding UTF8
        # Regex to match: #define MyAppVersion "1.1.7"
        $verPattern = '(?m)^#define\s+MyAppVersion\s+"(\d+)\.(\d+)\.(\d+)"'
        
        if ($issContent -match $verPattern) {
            $major = [int]$matches[1]
            $minor = [int]$matches[2]
            $patch = [int]$matches[3]

            # Increment logic: Patch + 1
            $patch++

            # Rollover logic: If patch > 9, reset patch to 0 and increment minor
            if ($patch -gt 9) {
                $patch = 0
                $minor++
            }

            $newVersion = "$major.$minor.$patch"
            
            # Replace with new version
            $issContent = $issContent -replace $verPattern, "#define MyAppVersion `"$newVersion`""
            
            # Write back to file
            Set-Content -Path $IssPath -Value $issContent -Encoding UTF8
            Write-Host "Version updated to: $newVersion" -ForegroundColor Green
        } else {
            Write-Warning "Could not find '#define MyAppVersion' pattern in version.iss. Skipping version update."
        }
    } catch {
        Write-Error "Failed to update version in version.iss: $($_.Exception.Message)"
    }
} else {
    Write-Warning "version.iss not found at '$IssPath'. Skipping version update."
}

# --- Resolve Build Type (new switch style takes precedence) ---
if ($Windowed -and $Console) {
    Write-Error "You cannot specify both -Windowed and -Console at the same time."
    exit 1
}

if ($DeployInstalled -and $Installer) {
    Write-Error "The -DeployInstalled flag cannot be combined with -Installer. Use -DeployInstalled for fast direct deployment, or -Installer for installer creation."
    exit 1
}

if ($DeployInstalled -and $Testing) {
    Write-Error "The -DeployInstalled flag cannot be combined with -Testing because both flags deploy to different target folders."
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

# --- Define Build Tool Paths ---
$PythonPath = Join-Path $PSScriptRoot ".venv\Scripts\python.exe"
$PyInstallerPath = Join-Path $PSScriptRoot ".venv\Scripts\pyinstaller.exe"

if (-not (Test-Path $PythonPath)) {
    Write-Error "Python not found at $PythonPath."
    Write-Error "Please ensure the virtual environment is set up."
    exit 1
}

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

if ($BuildMode -eq "Full") {
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
} else {
    Write-Host "Fast build: skipping 'pip install -r requirements.txt' (assuming venv is already set up)." -ForegroundColor Yellow
}

Write-Host "Using PyInstaller from: $PyInstallerPath" -ForegroundColor Green
Write-Host "Using Python from: $PythonPath" -ForegroundColor Green

# --- Build Variables ---
$AppName = "tidal-dl-gui"
$MainScript = "main.py" # Relative to $ProjectSourceDir
$IconFile = Join-Path $PackageDir "tidal_dl\assets\icons\icon-tidal-dl-gui.ico" # Relative to $PackageDir, use backslash for Windows path
$SplashImage = Join-Path $PackageDir "tidal_dl\assets\images\splash.png" # Path to the splash screen image

# --- Post-Build Copy Configuration ---
$BuildOutputDir        = Join-Path $ProjectSourceDir ("dist\" + $AppName)
$PostBuildExePath      = Join-Path $BuildOutputDir ($AppName + ".exe")
$PostBuildFolder       = Join-Path $BuildOutputDir "_internal"

# Test deployment target used by -Testing
$PostBuildTargetDir    = "C:\Users\imede.IME-DEKKER\OneDrive\Muziek\Tidal-dl-test"

# Actual installed app location used by -DeployInstalled
$InstalledDeployTargetDir = "C:\Users\imede.IME-DEKKER\OneDrive\Muziek\Tidal-dl"

# Windows process name without ".exe"
$AppProcessName = $AppName

function Stop-AppProcessIfRunning {
    param(
        [Parameter(Mandatory=$true)]
        [string]$ProcessName,

        [Parameter(Mandatory=$false)]
        [int]$TimeoutSeconds = 10
    )

    $runningProcesses = @(Get-Process -Name $ProcessName -ErrorAction SilentlyContinue)

    if ($runningProcesses.Count -eq 0) {
        Write-Host "No running '$ProcessName' process detected." -ForegroundColor Green
        return
    }

    Write-Warning "Detected running process '$ProcessName'. Force-closing before deployment..."

    foreach ($proc in $runningProcesses) {
        try {
            Write-Host "Stopping $($proc.ProcessName).exe with PID $($proc.Id)..." -ForegroundColor Yellow
            Stop-Process -Id $proc.Id -Force -ErrorAction Stop
        } catch {
            throw "Failed to stop process '$($proc.ProcessName).exe' with PID $($proc.Id): $($_.Exception.Message)"
        }
    }

    $deadline = (Get-Date).AddSeconds($TimeoutSeconds)

    do {
        Start-Sleep -Milliseconds 250
        $stillRunning = @(Get-Process -Name $ProcessName -ErrorAction SilentlyContinue)
    } while ($stillRunning.Count -gt 0 -and (Get-Date) -lt $deadline)

    if ($stillRunning.Count -gt 0) {
        $remainingPids = ($stillRunning | ForEach-Object { $_.Id }) -join ", "
        throw "Process '$ProcessName' is still running after $TimeoutSeconds second(s). Remaining PID(s): $remainingPids"
    }

    Write-Host "Process '$ProcessName' is closed." -ForegroundColor Green
}

function Copy-BuildArtifactsToTarget {
    param(
        [Parameter(Mandatory=$true)]
        [string]$SourceExePath,

        [Parameter(Mandatory=$true)]
        [string]$SourceInternalFolder,

        [Parameter(Mandatory=$true)]
        [string]$TargetDir,

        [Parameter(Mandatory=$true)]
        [string]$ProcessName,

        [Parameter(Mandatory=$false)]
        [switch]$ForceCloseApp
    )

    if ($ForceCloseApp) {
        Stop-AppProcessIfRunning -ProcessName $ProcessName
    }

    if (-not (Test-Path $SourceExePath)) {
        throw "Expected EXE not found at '$SourceExePath'."
    }

    if (-not (Test-Path $SourceInternalFolder)) {
        throw "Expected '_internal' folder not found at '$SourceInternalFolder'."
    }

    if (-not (Test-Path $TargetDir)) {
        Write-Host "Target directory does not exist. Creating: $TargetDir" -ForegroundColor Yellow
        New-Item -ItemType Directory -Path $TargetDir -Force | Out-Null
    }

    $targetExePath = Join-Path $TargetDir (Split-Path $SourceExePath -Leaf)
    $targetInternalFolder = Join-Path $TargetDir "_internal"

    Write-Host "Copying EXE:" -ForegroundColor Yellow
    Write-Host "  From: $SourceExePath"
    Write-Host "  To:   $targetExePath"
    Copy-Item -Path $SourceExePath -Destination $targetExePath -Force

    if (Test-Path $targetInternalFolder) {
        Write-Host "Replacing existing '_internal' folder:" -ForegroundColor Yellow
        Write-Host "  Removing: $targetInternalFolder"
        Remove-Item -Path $targetInternalFolder -Recurse -Force
    }

    Write-Host "Copying new '_internal' folder:" -ForegroundColor Yellow
    Write-Host "  From: $SourceInternalFolder"
    Write-Host "  To:   $TargetDir"
    Copy-Item -Path $SourceInternalFolder -Destination $TargetDir -Recurse -Force

    Write-Host "Deployment copy completed successfully." -ForegroundColor Green
}

# --- Start ---
Write-Host "-------------------------------------" -ForegroundColor Cyan
Write-Host "Starting Build for '$AppName'" -ForegroundColor Cyan
Write-Host "Build Type: $BuildType" -ForegroundColor Cyan
Write-Host "Build Mode: $BuildMode" -ForegroundColor Cyan
Write-Host "Testing Mode: $(if ($Testing) {'Enabled'} else {'Disabled'})" -ForegroundColor Cyan
Write-Host "Deploy Installed: $(if ($DeployInstalled) {'Enabled'} else {'Disabled'})" -ForegroundColor Cyan
Write-Host "Project Source: $ProjectSourceDir" -ForegroundColor Cyan
Write-Host "-------------------------------------"

# --- Cleanup ---
Write-Host "Cleaning up previous build artifacts..." -ForegroundColor Yellow

if ($BuildMode -eq "Full") {
    Write-Host "Full build: removing dist, build, spec, MANIFEST.in and *.egg-info" -ForegroundColor Yellow

    # Define paths to remove directly
    $DirectCleanupPaths = @(
        Join-Path $ProjectSourceDir "dist"
        Join-Path $ProjectSourceDir "build"
        Join-Path $ProjectSourceDir "$AppName.spec"
        Join-Path $PackageDir "dist"
        Join-Path $PackageDir "build"
        Join-Path $PackageDir "MANIFEST.in"
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
}
else {
    Write-Host "Fast build: keeping 'build' and most caches for PyInstaller speedup." -ForegroundColor Yellow

    # In fast mode, only wipe the app's dist folder + spec file so we get a clean output
    $FastCleanupPaths = @(
        Join-Path $ProjectSourceDir "dist"
        Join-Path $ProjectSourceDir "$AppName.spec"
    )

    foreach ($path in $FastCleanupPaths) {
        if (Test-Path $path) {
            Write-Host "Removing (fast mode): $path"
            Remove-Item -Path $path -Recurse -Force -ErrorAction SilentlyContinue
        }
    }
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
    # "--debug", "imports", # <-- UNCOMMENT AND CHANGE THIS LINE for import debugging
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
    "--add-data", "TIDALDL-PY/tidal_dl/metadata;tidal_dl/metadata"
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

# PyInstaller discovers package-provided hook directories in an isolated child
# process. qt_material imports itself for that discovery and warns unless a Qt
# binding is already loaded in that child process.
$PyInstallerSupportDir = Join-Path $ProjectSourceDir "build\pyinstaller-support"
$PyInstallerSiteCustomize = Join-Path $PyInstallerSupportDir "sitecustomize.py"
New-Item -ItemType Directory -Path $PyInstallerSupportDir -Force | Out-Null
Set-Content -Path $PyInstallerSiteCustomize -Encoding UTF8 -Value @'
# Imported by Python startup during the PyInstaller build.
try:
    import PyQt6  # noqa: F401
except Exception:
    pass
'@

# --- Execute PyInstaller ---
Write-Host "Running PyInstaller..." -ForegroundColor Yellow
$PyInstallerBootstrap = "import sys; import PyQt6; from PyInstaller.__main__ import run; run(sys.argv[1:])"
Write-Host "Command: $PythonPath -c `"$PyInstallerBootstrap`" $($pyinstallerArgs -join ' ')"

$PreviousPythonPath = $env:PYTHONPATH
try {
    if ([string]::IsNullOrEmpty($PreviousPythonPath)) {
        $env:PYTHONPATH = $PyInstallerSupportDir
    } else {
        $env:PYTHONPATH = "$PyInstallerSupportDir$([System.IO.Path]::PathSeparator)$PreviousPythonPath"
    }

    # Pre-import PyQt6 in the parent process; sitecustomize.py handles PyInstaller's child processes.
    & $PythonPath -c $PyInstallerBootstrap @pyinstallerArgs
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
finally {
    if ($null -eq $PreviousPythonPath) {
        Remove-Item Env:PYTHONPATH -ErrorAction SilentlyContinue
    } else {
        $env:PYTHONPATH = $PreviousPythonPath
    }
}

# --- Restore Original Location ---
Set-Location $ScriptDir
Write-Host "Restored working directory to: $ScriptDir"

# --- Post-Build: Deploy Artifacts to Actual Installed Location (CONDITIONAL) ---
if ($DeployInstalled) {
    Write-Host "-------------------------------------" -ForegroundColor Cyan
    Write-Host "Deploying build artifacts to actual installed location..." -ForegroundColor Cyan
    Write-Host "Target: $InstalledDeployTargetDir" -ForegroundColor Cyan
    Write-Host "Only the EXE and '_internal' folder will be replaced." -ForegroundColor Yellow
    Write-Host "Other files and settings in the target folder will be left untouched." -ForegroundColor Yellow

    try {
        Copy-BuildArtifactsToTarget `
            -SourceExePath $PostBuildExePath `
            -SourceInternalFolder $PostBuildFolder `
            -TargetDir $InstalledDeployTargetDir `
            -ProcessName $AppProcessName `
            -ForceCloseApp
    } catch {
        Write-Error "Failed to deploy to actual installed location: $($_.Exception.Message)"
        exit 1
    }
}

# --- Post-Build: Copy Artifacts to Test Location (CONDITIONAL) ---
# Logic:
# 1. If -Testing is ON and -Installer is OFF: Copy files manually (for raw EXE testing).
# 2. If -Testing is ON and -Installer is ON: Skip copy (Installer will handle deployment).
# 3. If -Testing is OFF: Do nothing.

if ($Testing -and -not $Installer) {
    Write-Host "-------------------------------------" -ForegroundColor Cyan
    Write-Host "Copying build artifacts to test location (Testing Mode)..." -ForegroundColor Cyan

    try {
        Copy-BuildArtifactsToTarget `
            -SourceExePath $PostBuildExePath `
            -SourceInternalFolder $PostBuildFolder `
            -TargetDir $PostBuildTargetDir `
            -ProcessName $AppProcessName
    } catch {
        Write-Error "Failed to copy artifacts to test location: $($_.Exception.Message)"
    }
} elseif ($Testing -and $Installer) {
    Write-Host "-------------------------------------" -ForegroundColor Cyan
    Write-Host "Testing mode enabled, but skipping direct file copy." -ForegroundColor Yellow
    Write-Host "Reason: -Installer flag is set. The installer will deploy files to the test location." -ForegroundColor Yellow
} else {
    Write-Host "-------------------------------------" -ForegroundColor Cyan
    Write-Host "Testing flag not set. Skipping copy to test location." -ForegroundColor Yellow
}

# --- Optional: Build Installer (Inno Setup) ---
if ($Installer) {
    Write-Host "-------------------------------------" -ForegroundColor Cyan
    Write-Host "Building Installer via Inno Setup..." -ForegroundColor Cyan

    # Check if iscc is available
    if (Get-Command "iscc" -ErrorAction SilentlyContinue) {
        
        # Define output directory relative to the script root (Portable)
        $InstallerOutputDir = "dist-installer"
        $InstallerFullOutputDir = Join-Path $ScriptDir $InstallerOutputDir

        # Ensure output directory exists
        if (-not (Test-Path $InstallerFullOutputDir)) {
            Write-Host "Creating installer output directory: $InstallerFullOutputDir" -ForegroundColor Yellow
            New-Item -ItemType Directory -Path $InstallerFullOutputDir -Force | Out-Null
        }

        # Construct Inno Setup Command
        # We use an array for arguments to handle quoting cleanly
        $isccArgs = @(
            "/O`"$InstallerOutputDir`"", # Output folder
            "/Qp"                        # Quiet with progress
        )

        # If Testing mode is active, pass the definition to Inno Setup
        if ($Testing) {
            Write-Host "Enabling 'TestingMode' in Inno Setup..." -ForegroundColor Yellow
            $isccArgs += "/DTestingMode"
        }

        # Add the script file
        $isccArgs += "`"setup_tidal_dl.iss`""

        Write-Host "Running Inno Setup Compiler..." -ForegroundColor Yellow
        Write-Host "Output Folder: $InstallerFullOutputDir"
        
        try {
            # Run iscc.exe
            $proc = Start-Process -FilePath "iscc" -ArgumentList $isccArgs -PassThru -NoNewWindow -Wait
            
            if ($proc.ExitCode -eq 0) {
                Write-Host "Installer created successfully!" -ForegroundColor Green
                Write-Host "Location: $InstallerFullOutputDir" -ForegroundColor Green
            } else {
                Write-Error "Inno Setup failed with exit code $($proc.ExitCode)."
            }
        } catch {
            Write-Error "Failed to execute Inno Setup: $($_.Exception.Message)"
        }
    } else {
        Write-Error "Inno Setup Compiler ('iscc') not found in PATH. Skipping installer build."
    }
}

Write-Host "-------------------------------------" -ForegroundColor Cyan
Write-Host "Build Script Finished." -ForegroundColor Cyan
Write-Host "Build completed at: $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')" -ForegroundColor Cyan
if ($Installer) {
    Write-Host "Installer available at: $(Join-Path $ScriptDir 'dist-installer')" -ForegroundColor Cyan
}
Write-Host "-------------------------------------"
