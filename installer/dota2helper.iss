; Установщик для Dota2Helper.
;
; Собирается из готовой папки dist\Dota2Helper (её делает PyInstaller
; по dota2helper.spec). Компилятор: Inno Setup 6.
;
;   iscc installer\dota2helper.iss
;
; Результат: installer\Output\Dota2Helper-setup.exe

#define AppName "Dota2Helper"
#define AppVersion "0.1.0"
#define AppPublisher "yst4l"
#define AppURL "https://github.com/yst4lpizdec/dota2helper"
#define AppExe "Dota2Helper.exe"

[Setup]
AppId={{9E2F5C3A-6B1D-4C8E-9A7F-2D4B6E8A1C30}
AppName={#AppName}
AppVersion={#AppVersion}
AppPublisher={#AppPublisher}
AppPublisherURL={#AppURL}
AppSupportURL={#AppURL}/issues
DefaultDirName={autopf}\{#AppName}
DefaultGroupName={#AppName}
DisableProgramGroupPage=yes
; Ставим для одного пользователя, без прав администратора: программе
; они не нужны, а лишнее окно «разрешить изменения» пугает.
PrivilegesRequired=lowest
OutputDir=Output
OutputBaseFilename=Dota2Helper-setup
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
SetupIconFile=..\backend\data\icons\app\app.ico
UninstallDisplayIcon={app}\{#AppExe}
; Chromium внутри — 64-битный, на 32-битной Windows работать не будет.
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible

[Languages]
Name: "russian"; MessagesFile: "compiler:Languages\Russian.isl"
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"
Name: "startup"; Description: "Запускать вместе с Windows"; GroupDescription: "Дополнительно:"; Flags: unchecked

[Files]
Source: "..\dist\Dota2Helper\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\{#AppName}"; Filename: "{app}\{#AppExe}"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\{#AppExe}"; Tasks: desktopicon
Name: "{userstartup}\{#AppName}"; Filename: "{app}\{#AppExe}"; Tasks: startup

[Run]
Filename: "{app}\{#AppExe}"; Description: "{cm:LaunchProgram,{#AppName}}"; Flags: nowait postinstall skipifsilent

[UninstallDelete]
; Настройки, журнал и снимки пакетов лежат отдельно от программы —
; удаляем их вместе с ней, иначе останется мусор в профиле.
Type: filesandordirs; Name: "{localappdata}\{#AppName}"
