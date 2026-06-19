; Script generated for Tidal-DL GUI Installer
; SEE THE DOCUMENTATION FOR DETAILS ON CREATING INNO SETUP SCRIPT FILES!

; -------------------------------------------------------------------------
; PATH CONFIGURATION
; -------------------------------------------------------------------------
; Default to the folder containing this installer script.
#ifndef ProjectRoot
  #define ProjectRoot SourcePath
#endif
#define DistDir     AddBackslash(ProjectRoot) + "Tidal-Media-Downloader\dist\tidal-dl-gui"
#define AssetsDir   AddBackslash(ProjectRoot) + "Tidal-Media-Downloader\TIDALDL-PY\tidal_dl\assets"

#define MyAppName "Tidal-DL GUI"
; Load the version from the external file
#include "version.iss"
#define MyAppPublisher "GeradeHouse"
#define MyAppExeName "tidal-dl-gui.exe"
#define MyAppIconName "icon-tidal-dl-gui.ico"

; -------------------------------------------------------------------------
; TESTING MODE LOGIC
; -------------------------------------------------------------------------
; If "TestingMode" is defined (passed via /DTestingMode from command line),
; we change the install folder and the music folder name.
#ifdef TestingMode
  #define MusicFolderName "Tidal-dl-test"
  ; In Testing mode, install directly to the Music\Tidal-dl-test folder
  ; This creates a duplicate, isolated version as requested.
  #define InstallPath "{%USERPROFILE}\Music\" + MusicFolderName
  #define AppNameSuffix " (Test)"
#else
  #define MusicFolderName "Tidal-dl"
  ; In Normal mode, install to Program Files
  #define InstallPath "{autopf}\" + MyAppName
  #define AppNameSuffix ""
#endif

; -------------------------------------------------------------------------
; APP ID (GUID)
; -------------------------------------------------------------------------
#define MyAppGuid "8829A3B4-C5D6-47E8-9901-23456789ABCD"
#define MyAppAppId "{{" + MyAppGuid + "}"

[Setup]
AppId={#MyAppAppId}
AppName={#MyAppName}{#AppNameSuffix}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}

; Install Directory (Dynamic based on TestingMode)
DefaultDirName={#InstallPath}
ArchitecturesInstallIn64BitMode=x64compatible

; Create Start Menu folder
DefaultGroupName={#MyAppName}{#AppNameSuffix}

; Uninstall settings
UninstallDisplayIcon={app}\{#MyAppExeName}

; -------------------------------------------------------------------------
; VISUAL SETTINGS
; -------------------------------------------------------------------------
; Light Mode Sidebar (Left)
WizardImageFile={#AssetsDir}\images\sidebar_image_light.png

; Dark Mode Sidebar (Left - Dynamic)
WizardImageFileDynamicDark={#AssetsDir}\images\sidebar_image_dark.png

; Light Mode Small Image (Top Right)
WizardSmallImageFile={#AssetsDir}\images\smallImage_light.png

; Dark Mode Small Image (Top Right - Dynamic)
WizardSmallImageFileDynamicDark={#AssetsDir}\images\SmallImage_dark.png

; Modern Windows 11 Style
WizardStyle=modern dynamic windows11 excludelightbuttons

; -------------------------------------------------------------------------
; UPDATE & INSTALL BEHAVIOR
; -------------------------------------------------------------------------
DirExistsWarning=no
CloseApplications=yes
DisableWelcomePage=no
Compression=lzma2
SolidCompression=yes
PrivilegesRequired=admin
OutputBaseFilename=TidalDL_Installer_v{#MyAppVersion}{#AppNameSuffix}
OutputDir={#ProjectRoot}

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
; Smart Desktop Shortcut (Hidden on Update)
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"; Flags: unchecked; Check: IsFreshInstall

[Dirs]
; Use {%USERPROFILE} to access the environment variable for C:\Users\<Name>
; The folder name changes dynamically based on TestingMode
Name: "{%USERPROFILE}\Music\{#MusicFolderName}"; Permissions: users-modify

[Files]
; 1. The Main Executable
Source: "{#DistDir}\{#MyAppExeName}"; DestDir: "{app}"; Flags: ignoreversion

; 2. The _internal folder (Recursive)
Source: "{#DistDir}\_internal\*"; DestDir: "{app}\_internal"; Flags: ignoreversion recursesubdirs createallsubdirs

; 3. The Icon File (We copy this to {app} so we can use it for the Music folder icon)
Source: "{#AssetsDir}\icons\{#MyAppIconName}"; DestDir: "{app}"; Flags: ignoreversion

[Icons]
; Start Menu Shortcut
Name: "{group}\{#MyAppName}{#AppNameSuffix}"; Filename: "{app}\{#MyAppExeName}"; IconFilename: "{app}\{#MyAppIconName}"

; Desktop Shortcut (Smart Logic)
Name: "{autodesktop}\{#MyAppName}{#AppNameSuffix}"; Filename: "{app}\{#MyAppExeName}"; IconFilename: "{app}\{#MyAppIconName}"; Check: ShouldCreateDesktopShortcut

[Run]
; Option to run the app immediately after installation finishes
; NOTE: This must be placed BEFORE the [Code] section!
Filename: "{app}\{#MyAppExeName}"; Description: "{cm:LaunchProgram,{#StringChange(MyAppName + AppNameSuffix, '&', '&&')}}"; Flags: nowait postinstall skipifsilent

[Code]
// Import Windows API functions for Window Focus and File Attributes
function SetForegroundWindow(hWnd: HWND): BOOL;
external 'SetForegroundWindow@user32.dll stdcall';

function SetWindowPos(hWnd: HWND; hWndInsertAfter: HWND; X, Y, cx, cy: Integer; uFlags: UINT): BOOL;
external 'SetWindowPos@user32.dll stdcall';

function SetFileAttributes(lpFileName: string; dwFileAttributes: DWORD): BOOL;
external 'SetFileAttributesW@kernel32.dll stdcall';

function WritePrivateProfileString(lpAppName, lpKeyName, lpString, lpFileName: String): BOOL;
external 'WritePrivateProfileStringW@kernel32.dll stdcall';

const
  HWND_TOPMOST = -1;
  HWND_NOTOPMOST = -2;
  SWP_NOMOVE = $0002;
  SWP_NOSIZE = $0001;
  SWP_SHOWWINDOW = $0040;
  
  // REMOVED: FILE_ATTRIBUTE_* constants are already defined internally by Inno Setup.

var
  IsUpgrade: Boolean;
  DesktopShortcutExists: Boolean;

// --------------------------------------------------------------------------
// 1. CUSTOM FOLDER ICON LOGIC
// --------------------------------------------------------------------------
procedure CustomizeMusicFolderIcon;
var
  MusicFolderPath, DesktopIniPath, IconPath: String;
begin
  // Use the Preprocessor variable MusicFolderName injected into the string
  MusicFolderPath := ExpandConstant('{%USERPROFILE}\Music\{#MusicFolderName}');
  DesktopIniPath  := MusicFolderPath + '\desktop.ini';
  IconPath        := ExpandConstant('{app}\{#MyAppIconName}');

  // Only proceed if the folder exists
  if DirExists(MusicFolderPath) then
  begin
    // 1. Create/Update desktop.ini
    WritePrivateProfileString('.ShellClassInfo', 'IconResource', IconPath + ',0', DesktopIniPath);
    WritePrivateProfileString('.ShellClassInfo', 'IconFile', IconPath, DesktopIniPath);
    WritePrivateProfileString('.ShellClassInfo', 'IconIndex', '0', DesktopIniPath);
    
    // 2. Set desktop.ini attributes to Hidden + System
    SetFileAttributes(DesktopIniPath, FILE_ATTRIBUTE_HIDDEN or FILE_ATTRIBUTE_SYSTEM);

    // 3. Set the Folder itself to Read-Only (Required trigger for Windows to read desktop.ini)
    SetFileAttributes(MusicFolderPath, FILE_ATTRIBUTE_READONLY);
  end;
end;

// --------------------------------------------------------------------------
// 2. INSTALLATION STEPS
// --------------------------------------------------------------------------
procedure CurStepChanged(CurStep: TSetupStep);
begin
  if CurStep = ssPostInstall then
  begin
    // Apply the custom icon to the Music folder after files are installed
    CustomizeMusicFolderIcon;
  end;
end;

// --------------------------------------------------------------------------
// 3. SMART UPDATE LOGIC
// --------------------------------------------------------------------------
function InitializeSetup(): Boolean;
var
  UninstallKey: String;
begin
  UninstallKey := 'SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall\{' + '{#MyAppGuid}' + '}_is1';
  IsUpgrade := RegKeyExists(HKEY_LOCAL_MACHINE, UninstallKey);

  if IsUpgrade then
  begin
    DesktopShortcutExists := FileExists(ExpandConstant('{autodesktop}\{#MyAppName}{#AppNameSuffix}.lnk'));
  end;

  Result := True;
end;

procedure InitializeWizard;
begin
  if IsUpgrade then
  begin
    WizardForm.WelcomeLabel1.Caption := 'Update found for {#MyAppName}{#AppNameSuffix}';
    WizardForm.WelcomeLabel2.Caption := 
      'Setup has detected an existing installation of {#MyAppName}{#AppNameSuffix}.' + #13#10 + #13#10 +
      'The installer will now update the application to version {#MyAppVersion}.' + #13#10 + #13#10 +
      'Your existing settings and shortcuts will be preserved. Click Next to continue.';
  end;
end;

// --------------------------------------------------------------------------
// 4. WINDOW FOCUS LOGIC
// --------------------------------------------------------------------------
procedure CurPageChanged(CurPageID: Integer);
begin
  if CurPageID = wpWelcome then
  begin
    SetWindowPos(WizardForm.Handle, HWND_TOPMOST, 0, 0, 0, 0, SWP_NOMOVE or SWP_NOSIZE or SWP_SHOWWINDOW);
    SetWindowPos(WizardForm.Handle, HWND_NOTOPMOST, 0, 0, 0, 0, SWP_NOMOVE or SWP_NOSIZE or SWP_SHOWWINDOW);
    SetForegroundWindow(WizardForm.Handle);
  end;
end;

// --------------------------------------------------------------------------
// 5. HELPER FUNCTIONS
// --------------------------------------------------------------------------
function IsFreshInstall: Boolean;
begin
  Result := not IsUpgrade;
end;

function ShouldCreateDesktopShortcut: Boolean;
begin
  if IsUpgrade then
    Result := DesktopShortcutExists
  else
    Result := WizardIsTaskSelected('desktopicon');
end;
