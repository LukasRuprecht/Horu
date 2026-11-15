# Builds Horu-Setup-<version>.exe, the Windows installer.
#
#   powershell -ExecutionPolicy ByPass -File packaging\build.ps1
#
# Needs uv and Inno Setup 6 (winget install JRSoftware.InnoSetup). Builds in build\ (a
# Python environment of its own, so a running Horu doesn't get in the way) and writes:
#   dist\Horu\                      the app, as a folder (run dist\Horu\Horu.exe to try it)
#                                   with LICENSE.txt and THIRD-PARTY-NOTICES.txt beside it
#   dist\Horu-Setup-<version>.exe   the installer
#
# Options:
#   -NoInstaller   stop after dist\Horu (no Inno Setup needed)

param([switch]$NoInstaller)

$ErrorActionPreference = "Stop"
$root = Split-Path $PSScriptRoot -Parent
$build = Join-Path $root "build"
$venv = Join-Path $build "venv"
$python = Join-Path $venv "Scripts\python.exe"
$env:HORU_BUILD = $build

function Step($text) { Write-Host "==> $text" -ForegroundColor Cyan }
function Run {
    & $args[0] $args[1..($args.Count - 1)]
    if ($LASTEXITCODE -ne 0) { throw "$($args[0]) exited with $LASTEXITCODE" }
}

$version = (Select-String -Path (Join-Path $root "src\horu\__init__.py") -Pattern '__version__ = "(.+)"').Matches[0].Groups[1].Value
Step "Building Horu $version"
New-Item -ItemType Directory -Path $build -Force | Out-Null

Step "Setting up the build environment (packages pinned by uv.lock)"
if (-not (Test-Path $python)) { Run uv venv $venv --python 3.12 }
$requirements = Join-Path $build "requirements.txt"
Run uv export --directory $root --frozen --no-dev --no-emit-project --no-hashes --quiet -o $requirements
Run uv pip install --python $python --quiet -r $requirements "pyinstaller==6.22.3"
Run uv pip install --python $python --quiet --no-deps --reinstall-package horu $root

Step "Downloading the OCR models into the build"
Run $python -c "from horu.text_sources import rapidocr_source as r; r.RapidOcrSource(); r.text_finder(640)"

# The config template as committed (a local copy may have been edited for development).
$template = Join-Path $build "config.example.toml"
$committed = git -C $root show HEAD:config.example.toml 2>$null
if ($LASTEXITCODE -eq 0) {
    [IO.File]::WriteAllText($template, ($committed -join "`n") + "`n")
} else {
    Copy-Item (Join-Path $root "config.example.toml") $template
}

# Version details shown in the exe's Properties.
$parts = ($version.Split(".") + @("0", "0", "0"))[0..3] -join ", "
@"
VSVersionInfo(
  ffi=FixedFileInfo(filevers=($parts), prodvers=($parts)),
  kids=[
    StringFileInfo([StringTable('040904B0', [
      StringStruct('ProductName', 'Horu'),
      StringStruct('FileDescription', 'Horu'),
      StringStruct('FileVersion', '$version'),
      StringStruct('ProductVersion', '$version'),
      StringStruct('OriginalFilename', 'Horu.exe'),
      StringStruct('LegalCopyright', 'MIT License')])]),
    VarFileInfo([VarStruct('Translation', [1033, 1200])])
  ]
)
"@ | Set-Content -Path (Join-Path $build "version.txt") -Encoding utf8

Step "Bundling the app (PyInstaller)"
Run $python -m PyInstaller --noconfirm --clean --log-level WARN `
    --distpath (Join-Path $root "dist") --workpath (Join-Path $build "pyinstaller") `
    (Join-Path $PSScriptRoot "horu.spec")
# The licences of everything bundled, beside Horu.exe (the installer takes it from there).
$notices = Join-Path $root "dist\Horu\THIRD-PARTY-NOTICES.txt"
Run $python (Join-Path $PSScriptRoot "notices.py") $requirements $notices
Copy-Item (Join-Path $root "LICENSE") (Join-Path $root "dist\Horu\LICENSE.txt")
Write-Host "Built dist\Horu\Horu.exe"
if ($NoInstaller) { return }

Step "Making the installer (Inno Setup)"
$iscc = @(
    (Get-Command iscc -ErrorAction SilentlyContinue | Select-Object -ExpandProperty Source -First 1),
    (Join-Path $env:LOCALAPPDATA "Programs\Inno Setup 6\ISCC.exe"),
    (Join-Path ${env:ProgramFiles(x86)} "Inno Setup 6\ISCC.exe"),
    (Join-Path $env:ProgramFiles "Inno Setup 6\ISCC.exe")
) | Where-Object { $_ -and (Test-Path $_) } | Select-Object -First 1
if (-not $iscc) { throw "Inno Setup 6 isn't installed: winget install JRSoftware.InnoSetup (or use -NoInstaller)." }
Run $iscc /Q "/DVersion=$version" (Join-Path $PSScriptRoot "horu.iss")
Write-Host "Built dist\Horu-Setup-$version.exe" -ForegroundColor Green
