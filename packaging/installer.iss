#ifndef AppVersion
  #define AppVersion "0.1.0"
#endif
#ifndef BundleDir
  #define BundleDir "..\dist\GolfTraceOpen"
#endif
#ifndef ReleaseDir
  #define ReleaseDir "..\release"
#endif
#ifndef AppIcon
  #define AppIcon "..\src\golftrace\assets\golftrace.ico"
#endif

[Setup]
AppId={{AE593493-321C-4F1E-9FD7-C364E84B0C1D}
AppName=GolfTraceOpen
AppVersion={#AppVersion}
AppPublisher=GolfTraceOpen contributors
DefaultDirName={localappdata}\Programs\GolfTraceOpen
DefaultGroupName=GolfTraceOpen
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible and not arm64
ArchitecturesInstallIn64BitMode=x64compatible and not arm64
MinVersion=10.0.19041
OutputDir={#ReleaseDir}
OutputBaseFilename=GolfTraceOpen-Setup-{#AppVersion}-win-x64
Compression=lzma2/fast
SolidCompression=yes
WizardStyle=modern
SetupIconFile={#AppIcon}
UninstallDisplayIcon={app}\GolfTraceOpen.exe
CloseApplications=yes
RestartApplications=no
SetupLogging=yes

[Tasks]
Name: "desktopicon"; Description: "Create a desktop shortcut"; GroupDescription: "Additional shortcuts:"; Flags: unchecked

[Files]
Source: "{#BundleDir}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{autoprograms}\GolfTraceOpen"; Filename: "{app}\GolfTraceOpen.exe"
Name: "{autodesktop}\GolfTraceOpen"; Filename: "{app}\GolfTraceOpen.exe"; Tasks: desktopicon

[Run]
Filename: "{app}\GolfTraceOpen.exe"; Description: "Launch GolfTraceOpen"; Flags: nowait postinstall skipifsilent
