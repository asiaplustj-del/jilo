; Установщик Jilo для Windows (Inno Setup 6)
#define AppVer "1.0.1"
[Setup]
AppId={{6E1B3C1E-2F8A-4C6B-9B1E-7A1E4A2C0B11}
AppName=Jilo
AppVersion={#AppVer}
AppPublisher=Asia-Plus Studio
DefaultDirName={autopf}\Jilo
DefaultGroupName=Jilo
DisableProgramGroupPage=yes
OutputDir=..\dist
OutputBaseFilename=Jilo-Setup-{#AppVer}
SetupIconFile=..\assets\jilo.ico
UninstallDisplayIcon={app}\Jilo.exe
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog
ArchitecturesInstallIn64BitMode=x64compatible

[Languages]
Name: "ru"; MessagesFile: "compiler:Languages\Russian.isl"
Name: "en"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"

[Files]
Source: "..\dist\Jilo\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\Jilo"; Filename: "{app}\Jilo.exe"
Name: "{autodesktop}\Jilo"; Filename: "{app}\Jilo.exe"; Tasks: desktopicon

[Run]
Filename: "{app}\Jilo.exe"; Description: "{cm:LaunchProgram,Jilo}"; Flags: nowait postinstall skipifsilent
