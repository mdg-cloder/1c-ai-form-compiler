<#
.SYNOPSIS
  Builds the Windows release of one-c-form-compiler (PyInstaller ONEDIR, no .py sources).

.DESCRIPTION
  1. Creates (or reuses) the build virtual environment .venv-build in the repository root.
  2. Installs the package and pyinstaller==6.22.3 into it.
  3. Runs PyInstaller with packaging/one-c-form-compiler.spec (work files under build/).
  4. Assembles OutDir:
       one-c-form-compiler/                    the executable folder
       LICENSE
       files from packaging/release/          user instructions, third-party notices, env example
       one-c-form-compiler-<version>-win-x64.zip  all of the above under one folder
                                               one-c-form-compiler-<version>-win-x64/ (built with Python
                                               zipfile: forward-slash entry names)
       SHA256SUMS.txt

  Compatible with Windows PowerShell 5.1 and PowerShell 7.

.PARAMETER Python
  Python 3.14 used to create the build environment (default: python).

.PARAMETER OutDir
  Release folder (default: dist in the repository root). Only the items listed above are replaced there.

.EXAMPLE
  powershell -ExecutionPolicy Bypass -File packaging\build_release.ps1
.EXAMPLE
  powershell -ExecutionPolicy Bypass -File packaging\build_release.ps1 -Python py -OutDir D:\release
#>
[CmdletBinding()]
param(
    [string]$Python = "python",
    [string]$OutDir = ""
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version 2.0

function Invoke-Checked {
    param([string]$Exe, [string[]]$Arguments, [string]$Step)
    Write-Host "> $Step"
    & $Exe @Arguments
    if ($LASTEXITCODE -ne 0) {
        throw "$Step failed (exit code $LASTEXITCODE)"
    }
}

$Root = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$AppName = "one-c-form-compiler"
$Spec = Join-Path $PSScriptRoot "$AppName.spec"
$ReleaseDocs = Join-Path $PSScriptRoot "release"

if ([string]::IsNullOrEmpty($OutDir)) {
    $OutDir = Join-Path $Root "dist"
} elseif (-not [System.IO.Path]::IsPathRooted($OutDir)) {
    $OutDir = Join-Path (Get-Location).Path $OutDir
}
$OutDir = [System.IO.Path]::GetFullPath($OutDir)

# Version from src/one_c_form_compiler/__init__.py
$InitText = Get-Content -Raw -Encoding UTF8 (Join-Path $Root "src\one_c_form_compiler\__init__.py")
$Match = [regex]::Match($InitText, '__version__\s*=\s*"([^"]+)"')
if (-not $Match.Success) {
    throw "__version__ not found in src/one_c_form_compiler/__init__.py"
}
$Version = $Match.Groups[1].Value
$ReleaseName = "$AppName-$Version-win-x64"
Write-Host "Building $AppName $Version -> $OutDir"

# 1-2. Build environment
$Venv = Join-Path $Root ".venv-build"
$VenvPython = Join-Path $Venv "Scripts\python.exe"
if (-not (Test-Path $VenvPython)) {
    Invoke-Checked $Python @("-m", "venv", $Venv) "create .venv-build"
}
Invoke-Checked $VenvPython @("-m", "pip", "install", "--disable-pip-version-check", "--upgrade", "pip") "upgrade pip"
# Install from a copy under build/ so setuptools does not write build/ or *.egg-info into src/.
$BuildRoot = Join-Path $Root "build"
$PkgSrc = Join-Path $BuildRoot "pkgsrc"
if (Test-Path $PkgSrc) {
    Remove-Item -Recurse -Force $PkgSrc
}
New-Item -ItemType Directory -Force $PkgSrc | Out-Null
foreach ($Name in @("pyproject.toml", "README.md", "LICENSE")) {
    Copy-Item (Join-Path $Root $Name) $PkgSrc
}
Copy-Item -Recurse (Join-Path $Root "src") (Join-Path $PkgSrc "src")
Get-ChildItem -Recurse -Directory -Force $PkgSrc -Filter "__pycache__" | Remove-Item -Recurse -Force
Invoke-Checked $VenvPython @("-m", "pip", "install", "--disable-pip-version-check", $PkgSrc, "pyinstaller==6.22.3") "install package and PyInstaller"

# 3. PyInstaller
$PyiDist = Join-Path $BuildRoot "pyinstaller\dist"
$PyiWork = Join-Path $BuildRoot "pyinstaller\work"
Invoke-Checked $VenvPython @("-m", "PyInstaller", "--noconfirm", "--clean",
    "--distpath", $PyiDist, "--workpath", $PyiWork, $Spec) "PyInstaller"
$AppDir = Join-Path $PyiDist $AppName
if (-not (Test-Path (Join-Path $AppDir "$AppName.exe"))) {
    throw "PyInstaller did not produce $AppName.exe"
}

# 4. Stage the release content
$Stage = Join-Path $BuildRoot "release\$ReleaseName"
if (Test-Path $Stage) {
    Remove-Item -Recurse -Force $Stage
}
New-Item -ItemType Directory -Force $Stage | Out-Null
Copy-Item -Recurse $AppDir (Join-Path $Stage $AppName)
Copy-Item (Join-Path $Root "LICENSE") $Stage
if (Test-Path $ReleaseDocs) {
    Get-ChildItem -File $ReleaseDocs | ForEach-Object { Copy-Item $_.FullName $Stage }
} else {
    Write-Warning "packaging/release not found: release instructions are not included"
}

$ZipName = "$ReleaseName.zip"
$StageZip = Join-Path $BuildRoot "release\$ZipName"
if (Test-Path $StageZip) {
    Remove-Item -Force $StageZip
}
# Python zipfile, not Compress-Archive / .NET ZipFile: on Windows PowerShell 5.1 those can write entry
# names with backslashes, which unzip tools on other systems turn into flat file names.
# One top-level folder: <ReleaseName>/one-c-form-compiler/one-c-form-compiler.exe, LICENSE, docs.
$ZipScript = @'
import os, sys, zipfile
stage, target = sys.argv[1], sys.argv[2]
top = os.path.basename(os.path.normpath(stage))
with zipfile.ZipFile(target, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
    for folder, dirs, files in os.walk(stage):
        dirs.sort()
        for name in sorted(files):
            path = os.path.join(folder, name)
            relative = os.path.relpath(path, stage).replace(os.sep, "/")
            archive.write(path, top + "/" + relative)
with zipfile.ZipFile(target) as archive:
    bad = [item for item in archive.namelist() if "\\" in item or not item.startswith(top + "/")]
    if bad:
        sys.exit("bad zip entry names: " + ", ".join(bad[:5]))
'@
# Run from a file: Windows PowerShell 5.1 does not escape the double quotes of a "-c" argument.
$ZipScriptPath = Join-Path $BuildRoot "make_zip.py"
[System.IO.File]::WriteAllText($ZipScriptPath, $ZipScript, (New-Object System.Text.UTF8Encoding($false)))
Invoke-Checked $VenvPython @($ZipScriptPath, $Stage, $StageZip) "create $ZipName"

# Publish to OutDir: replace only what this script produces
New-Item -ItemType Directory -Force $OutDir | Out-Null
$Produced = @($AppName, "LICENSE", "SHA256SUMS.txt", $ZipName)
if (Test-Path $ReleaseDocs) {
    $Produced += @(Get-ChildItem -File $ReleaseDocs | ForEach-Object { $_.Name })
}
foreach ($Name in $Produced) {
    $Target = Join-Path $OutDir $Name
    if (Test-Path $Target) {
        Remove-Item -Recurse -Force $Target
    }
}
Get-ChildItem $Stage | ForEach-Object { Copy-Item -Recurse $_.FullName $OutDir }
Copy-Item $StageZip $OutDir

$Hash = (Get-FileHash -Algorithm SHA256 (Join-Path $OutDir $ZipName)).Hash.ToLowerInvariant()
Set-Content -Encoding Ascii -Path (Join-Path $OutDir "SHA256SUMS.txt") -Value "$Hash  $ZipName"

Write-Host "Release ready: $OutDir"
Write-Host "  $AppName\$AppName.exe"
Write-Host "  $ZipName  sha256 $Hash"
