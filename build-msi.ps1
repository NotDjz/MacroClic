#Requires -Version 7
# Builds dist\MacroClic.exe, then a per-user installer: dist\MacroClic-<version>-x64.msi.
# Needs Python 3.13 and WiX 7 (dotnet tool install -g wix). The version is the latest tag unless given.
param([string]$Version)
$ErrorActionPreference = 'Stop'
Set-Location $PSScriptRoot

if (-not $Version) {
    $Version = (git describe --tags --abbrev=0) -replace '^v', ''
    while ($Version.Split('.').Count -lt 3) { $Version += '.0' }
}

# A failed build must not leave the previous exe to be packaged.
Remove-Item dist\MacroClic.exe -ErrorAction SilentlyContinue
py -3.13 -m pip install -q -r requirements.txt pyinstaller
# Keep these flags in sync with the README, CLAUDE.md and .github/workflows/release.yml.
py -3.13 -m PyInstaller --noconfirm --onefile --windowed --name MacroClic --icon macroclic.ico `
    --add-data "macroclic.ico;." --collect-data sv_ttk macroclic.py
if ($LASTEXITCODE -or -not (Test-Path dist\MacroClic.exe)) { throw 'PyInstaller did not produce dist\MacroClic.exe' }

wix build installer\macroclic.wxs -arch x64 -d Version=$Version -o "dist\MacroClic-$Version-x64.msi"
if ($LASTEXITCODE) { exit $LASTEXITCODE }
"dist\MacroClic-$Version-x64.msi"
