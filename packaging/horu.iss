; Inno Setup script for Horu-Setup-<version>.exe. Built by packaging\build.ps1, which passes
; the version (/DVersion=...) after bundling the app into dist\Horu.
;
; Installs for the current user only (no admin prompt) into %LOCALAPPDATA%\Programs\Horu.
; Settings, the dictionary, and saved words live in %LOCALAPPDATA%\Horu and are kept on
; update; uninstalling asks before removing them.

#ifndef Version
  #define Version "0.0.0"
#endif

[Setup]
; Identifies Horu to Windows across versions: never change it.
AppId={{A758F975-C08E-4B13-9A8E-2D6CD4EA85BE}
AppName=Horu
AppVersion={#Version}
AppVerName=Horu {#Version}
AppPublisher=Horu
AppPublisherURL=https://github.com/LukasRuprecht/Horu
AppSupportURL=https://github.com/LukasRuprecht/Horu/issues
DefaultDirName={autopf}\Horu
DisableProgramGroupPage=yes
DisableDirPage=auto
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=commandline
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=10.0
OutputDir=..\dist
OutputBaseFilename=Horu-Setup-{#Version}
SetupIconFile=..\src\horu\resources\horu.ico
UninstallDisplayIcon={app}\Horu.exe
UninstallDisplayName=Horu
WizardSmallImageFile=..\src\horu\resources\horu.png
WizardStyle=modern
Compression=lzma2/ultra64
SolidCompression=yes
LZMANumBlockThreads=2
; The 700 MB app needs more memory to compress than the compiler process has.
LZMAUseSeparateProcess=yes
; Close a running Horu before replacing its files.
CloseApplications=force
RestartApplications=no

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"

[InstallDelete]
; An update replaces the whole app: no files left over from the version before.
Type: filesandordirs; Name: "{app}\_internal"

[Files]
Source: "..\dist\Horu\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{autoprograms}\Horu"; Filename: "{app}\Horu.exe"; Comment: "A Japanese study buddy for games"
Name: "{autodesktop}\Horu"; Filename: "{app}\Horu.exe"; Comment: "A Japanese study buddy for games"; Tasks: desktopicon

[Run]
Filename: "{app}\Horu.exe"; Description: "{cm:LaunchProgram,Horu}"; Flags: nowait postinstall skipifsilent

[Code]
const
  WebView2Key = 'SOFTWARE\Microsoft\EdgeUpdate\Clients\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}';
  WebView2Url = 'https://developer.microsoft.com/microsoft-edge/webview2/';

function HasWebView2: Boolean;
var
  Version: String;
begin
  Result :=
    (RegQueryStringValue(HKLM, 'SOFTWARE\WOW6432Node\Microsoft\EdgeUpdate\Clients\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}', 'pv', Version) and (Version <> '') and (Version <> '0.0.0.0')) or
    (RegQueryStringValue(HKLM, WebView2Key, 'pv', Version) and (Version <> '') and (Version <> '0.0.0.0')) or
    (RegQueryStringValue(HKCU, WebView2Key, 'pv', Version) and (Version <> '') and (Version <> '0.0.0.0'));
end;

procedure CurStepChanged(CurStep: TSetupStep);
var
  ErrorCode: Integer;
begin
  // Horu's window is drawn by Microsoft Edge WebView2, part of Windows 11 and most Windows 10.
  if (CurStep = ssPostInstall) and not HasWebView2 and not WizardSilent then
    if MsgBox('Horu needs Microsoft Edge WebView2, which isn''t on this PC yet.' + #13#10#13#10 +
              'Open Microsoft''s download page now? (Choose "Evergreen Bootstrapper".)',
              mbConfirmation, MB_YESNO) = IDYES then
      ShellExec('open', WebView2Url, '', '', SW_SHOWNORMAL, ewNoWait, ErrorCode);
end;

procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
var
  Data: String;
begin
  Data := ExpandConstant('{localappdata}\Horu');
  if (CurUninstallStep = usPostUninstall) and DirExists(Data) and not UninstallSilent then
    if MsgBox('Also delete your Horu data?' + #13#10#13#10 +
              'This is your settings, saved words, study progress, learned names, and the downloaded dictionary, in:' + #13#10 + Data + #13#10#13#10 +
              'Choose No to keep them for when you install Horu again.',
              mbConfirmation, MB_YESNO or MB_DEFBUTTON2) = IDYES then
      DelTree(Data, True, True, True);
end;
