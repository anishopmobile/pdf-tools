# Builds per-user installer, from a successful dist/PDFTools/ EXE build.
$ErrorActionPreference = 'Stop'
Set-Location (Resolve-Path (Join-Path $PSScriptRoot '..'))
if (-not (Test-Path 'dist\PDFTools\PDFTools.exe')) { throw 'First run scripts\build_windows.ps1' }
$paths = @(
    "${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe",
    "$env:ProgramFiles\Inno Setup 6\ISCC.exe"
)
$iscc = $paths | Where-Object { Test-Path $_ } | Select-Object -First 1
if (-not $iscc) { $command = Get-Command ISCC.exe -ErrorAction SilentlyContinue; if ($command) { $iscc = $command.Source } }
if (-not $iscc) { throw 'Install Inno Setup 6, then rerun this script.' }
& $iscc 'installer\PDFTools.iss'
if ($LASTEXITCODE -ne 0) { throw 'Inno Setup build failed.' }
if (-not (Test-Path 'dist\installer\PDFTools-Setup.exe')) { throw 'Installer output is missing.' }
Write-Host 'Installer ready: dist\installer\PDFTools-Setup.exe'
