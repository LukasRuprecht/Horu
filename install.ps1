# Horu installer for Windows.
#
#   powershell -ExecutionPolicy ByPass -c "irm https://raw.githubusercontent.com/LukasRuprecht/Horu/main/install.ps1 | iex"
#
# Installs uv (if needed), downloads Horu into %LOCALAPPDATA%\Horu, sets up its Python and
# packages, and adds Horu to the Start menu and desktop. Run it again to update: your
# settings, dictionary, and learned names are kept. The dictionary and OCR models download
# the first time Horu starts.
#
# Options (when saved as a file and run with -File):
#   -Dir <path>      install somewhere else
#   -Source <path>   install from a local copy of Horu, or a .zip URL, instead of GitHub
#   -NoShortcuts     don't add Start menu and desktop shortcuts
#   -Uninstall       remove Horu and its shortcuts (asks first; your data goes too)

param(
    [string]$Dir = (Join-Path $env:LOCALAPPDATA "Horu"),
    [string]$Source = "https://github.com/LukasRuprecht/Horu/archive/refs/heads/main.zip",
    [switch]$NoShortcuts,
    [switch]$Uninstall
)

$ErrorActionPreference = "Stop"
$ProgressPreference = "SilentlyContinue"  # Invoke-WebRequest is far slower with its progress bar

# Kept on update: what Horu made or learned while running.
$Keep = @("data", "captures", "debug", "config.toml", ".env", ".venv")
$Shortcuts = @(
    (Join-Path ([Environment]::GetFolderPath("Programs")) "Horu.lnk"),
    (Join-Path ([Environment]::GetFolderPath("Desktop")) "Horu.lnk")
)

function Step($text) { Write-Host "==> $text" -ForegroundColor Cyan }

function Stop-IfRunning {
    $running = Get-Process -Name "horu-app", "horu" -ErrorAction SilentlyContinue |
        Where-Object { $_.Path -and $_.Path.StartsWith($Dir, [StringComparison]::OrdinalIgnoreCase) }
    if ($running) {
        throw "Horu is running. Close it, then run the installer again."
    }
}

if ($Uninstall) {
    Stop-IfRunning
    $answer = Read-Host "Remove Horu from $Dir, with your settings and learned names? [y/N]"
    if ($answer -notmatch "^[yY]") { Write-Host "Nothing removed."; return }
    foreach ($link in $Shortcuts) { Remove-Item $link -ErrorAction SilentlyContinue }
    Remove-Item $Dir -Recurse -Force -ErrorAction SilentlyContinue
    Write-Host "Horu is removed. API keys you saved stay in Windows Credential Manager (entries named horu)."
    return
}

# --- uv: installs Python and Horu's packages -------------------------------------------
$uv = Get-Command uv -ErrorAction SilentlyContinue | Select-Object -ExpandProperty Source -First 1
if (-not $uv) {
    $uv = Join-Path $env:USERPROFILE ".local\bin\uv.exe"
    if (-not (Test-Path $uv)) {
        Step "Installing uv (Python package manager, from astral.sh)"
        Invoke-RestMethod https://astral.sh/uv/install.ps1 | Invoke-Expression
    }
}
if (-not (Test-Path $uv)) { throw "uv didn't install. See https://docs.astral.sh/uv/ and try again." }

# --- Horu's files ------------------------------------------------------------------------
Stop-IfRunning
$staging = Join-Path ([IO.Path]::GetTempPath()) ("horu-install-" + [Guid]::NewGuid().ToString("N"))
New-Item -ItemType Directory -Path $staging | Out-Null
try {
    if (Test-Path $Source -PathType Container) {
        Step "Copying Horu from $Source"
        $from = (Resolve-Path $Source).Path
    } else {
        Step "Downloading Horu"
        $zip = Join-Path $staging "horu.zip"
        if (Test-Path $Source -PathType Leaf) { Copy-Item $Source $zip } else { Invoke-WebRequest $Source -OutFile $zip }
        Expand-Archive $zip -DestinationPath (Join-Path $staging "x")
        # GitHub's zip holds one folder (Horu-main).
        $from = (Get-ChildItem (Join-Path $staging "x") -Directory | Select-Object -First 1).FullName
    }
    New-Item -ItemType Directory -Path $Dir -Force | Out-Null
    # Replace Horu's own files; leave what it made while running.
    Get-ChildItem $Dir -Force | Where-Object { $Keep -notcontains $_.Name } | Remove-Item -Recurse -Force
    Get-ChildItem $from -Force | Where-Object { $Keep -notcontains $_.Name -and $_.Name -ne ".git" } |
        Copy-Item -Destination $Dir -Recurse -Force
} finally {
    Remove-Item $staging -Recurse -Force -ErrorAction SilentlyContinue
}

# --- Python and packages ---------------------------------------------------------------
Step "Setting up Python and Horu's packages (the first time downloads about 1 GB)"
& $uv sync --directory $Dir --frozen --no-dev
if ($LASTEXITCODE -ne 0) { throw "Setting up packages failed (uv sync exited with $LASTEXITCODE)." }
$app = Join-Path $Dir ".venv\Scripts\horu-app.exe"
if (-not (Test-Path $app)) { throw "Setup finished but $app is missing." }

# --- Shortcuts ---------------------------------------------------------------------------
if (-not $NoShortcuts) {
    Step "Adding Horu to the Start menu and desktop"
    $shell = New-Object -ComObject WScript.Shell
    foreach ($link in $Shortcuts) {
        $shortcut = $shell.CreateShortcut($link)
        $shortcut.TargetPath = $app
        $shortcut.WorkingDirectory = $Dir  # Horu keeps config.toml and data\ here
        $shortcut.Description = "Horu: a Japanese study buddy for games"
        $icon = Join-Path $Dir "src\horu\resources\horu.ico"
        if (Test-Path $icon) { $shortcut.IconLocation = $icon }
        $shortcut.Save()
    }
}

Write-Host ""
Write-Host "Horu is installed in $Dir." -ForegroundColor Green
if (-not $NoShortcuts) { Write-Host "Start it from the Start menu or the desktop shortcut." }
Write-Host "The first start downloads the dictionary and OCR models (about 150 MB)."
