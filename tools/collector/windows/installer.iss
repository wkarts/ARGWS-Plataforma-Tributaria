; Compiled on Windows CI from verified GUI, worker and pinned WinSW payloads.
; APP_VERSION and SOURCE_ROOT are passed with ISCC /D switches.
#ifndef APP_VERSION
  #error APP_VERSION is required
#endif
#ifndef SOURCE_ROOT
  #error SOURCE_ROOT is required
#endif
#ifndef APP_DISPLAY_NAME
  #define APP_DISPLAY_NAME "Fiscal XML Collector"
#endif
#ifndef APP_PUBLISHER
  #define APP_PUBLISHER "Fiscal XML Collector"
#endif
#ifndef APP_ICON_PATH
  #define APP_ICON_PATH SOURCE_ROOT + "\tools\collector\windows\collector.ico"
#endif

[Setup]
AppId={{ADBE267C-0713-4321-8A8A-3A5742672F3C}
AppName={#APP_DISPLAY_NAME}
AppVersion={#APP_VERSION}
AppVerName={#APP_DISPLAY_NAME} {#APP_VERSION}
AppPublisher={#APP_PUBLISHER}
DefaultDirName={autopf64}\FiscalXmlCollector
DefaultGroupName={#APP_DISPLAY_NAME}
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
PrivilegesRequired=admin
OutputDir={#SOURCE_ROOT}\dist\collector
OutputBaseFilename=fiscal-xml-collector-setup-windows-x64
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
SetupIconFile={#APP_ICON_PATH}
UninstallDisplayIcon={app}\fiscal-xml-gui-windows-x64.exe
CloseApplications=yes
RestartApplications=no

[Files]
Source: "{#SOURCE_ROOT}\dist\collector\fiscal-xml-gui-windows-x64.exe"; DestDir: "{app}"; Flags: ignoreversion
Source: "{#SOURCE_ROOT}\dist\collector\fiscal-xml-worker-windows-x64.exe"; DestDir: "{app}"; Flags: ignoreversion
Source: "{#SOURCE_ROOT}\dist\collector\FiscalXmlCollector.exe"; DestDir: "{app}"; Flags: ignoreversion
Source: "{#SOURCE_ROOT}\tools\collector\windows\manage-service.ps1"; DestDir: "{app}"; Flags: ignoreversion
Source: "{#SOURCE_ROOT}\tools\collector\windows\LICENSE-WinSW.txt"; DestDir: "{app}"; Flags: ignoreversion
Source: "{#SOURCE_ROOT}\tools\collector\windows\README-installer.md"; DestDir: "{app}"; Flags: ignoreversion

[Dirs]
Name: "{commonappdata}\FiscalXmlCollector"

[Icons]
Name: "{autoprograms}\{#APP_DISPLAY_NAME}"; Filename: "{app}\fiscal-xml-gui-windows-x64.exe"; WorkingDir: "{app}"
Name: "{autodesktop}\{#APP_DISPLAY_NAME}"; Filename: "{app}\fiscal-xml-gui-windows-x64.exe"; Tasks: desktopicon

[Tasks]
Name: desktopicon; Description: "Criar atalho na área de trabalho"

[Run]
Filename: "{app}\fiscal-xml-gui-windows-x64.exe"; Description: "Abrir {#APP_DISPLAY_NAME} para cadastrar empresas"; Flags: postinstall skipifsilent runascurrentuser nowait

[Code]
var
  ServiceWasRunning: Boolean;

procedure RunManager(const ActionName: String);
var
  ExitCode: Integer;
  Params: String;
begin
  Params := '-NoProfile -NonInteractive -ExecutionPolicy Bypass -File "' +
    ExpandConstant('{app}\manage-service.ps1') + '" -Action ' + ActionName +
    ' -Home "' + ExpandConstant('{commonappdata}\FiscalXmlCollector') + '"';
  if (not Exec(ExpandConstant('{sys}\WindowsPowerShell\v1.0\powershell.exe'),
    Params, '', SW_HIDE, ewWaitUntilTerminated, ExitCode)) or (ExitCode <> 0) then
    RaiseException('A operação ' + ActionName + ' do serviço falhou. Verifique o registro da instalação.');
end;

function PrepareToInstall(var NeedsRestart: Boolean): String;
var
  ExitCode: Integer;
  PowerShell: String;
begin
  Result := '';
  ServiceWasRunning := False;
  PowerShell := ExpandConstant('{sys}\WindowsPowerShell\v1.0\powershell.exe');
  if not Exec(PowerShell, '-NoProfile -Command "if ((Get-Service -Name FiscalXmlCollector -ErrorAction SilentlyContinue).Status -eq ''Running'') { exit 0 } else { exit 1 }"', '', SW_HIDE, ewWaitUntilTerminated, ExitCode) then Exit;
  ServiceWasRunning := ExitCode = 0;
  if ServiceWasRunning then begin
    if (not Exec(PowerShell, '-NoProfile -NonInteractive -Command "Stop-Service -Name FiscalXmlCollector -ErrorAction Stop; (Get-Service -Name FiscalXmlCollector).WaitForStatus(''Stopped'', [TimeSpan]::FromSeconds(45))"', '', SW_HIDE, ewWaitUntilTerminated, ExitCode)) or (ExitCode <> 0) then
      Result := 'Não foi possível parar o serviço para atualizar os binários. Tente novamente após parar o serviço.';
  end;
end;

procedure CurStepChanged(CurStep: TSetupStep);
begin
  if CurStep = ssPostInstall then begin
    { Fail installation if private storage could not be protected. }
    RunManager('Protect');
    if ServiceWasRunning then RunManager('Start');
  end;
end;

procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
begin
  if CurUninstallStep = usUninstall then RunManager('Uninstall');
end;
