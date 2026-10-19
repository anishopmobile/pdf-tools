# PowerShell, run from anywhere on a Windows 10/11 machine with Python 3.12.
$ErrorActionPreference = 'Stop'
Set-Location (Resolve-Path (Join-Path $PSScriptRoot '..'))
if (-not (Test-Path '.venv\Scripts\python.exe')) {
    py -3.12 -m venv .venv
    if ($LASTEXITCODE -ne 0) { throw 'Python 3.12 is required. Install it from python.org.' }
}
$python = Join-Path (Get-Location) '.venv\Scripts\python.exe'
& $python -m pip install --requirement requirements-build.txt
if ($LASTEXITCODE -ne 0) { throw 'Dependency installation failed.' }
$env:QT_QPA_PLATFORM = 'offscreen'
& $python -m pytest -q
if ($LASTEXITCODE -ne 0) { throw 'Tests failed; build cancelled.' }
& $python -m compileall -q pdf_tools tests
if ($LASTEXITCODE -ne 0) { throw 'Compile check failed; build cancelled.' }
& $python -m PyInstaller --noconfirm --clean --windowed --onedir --name PDFTools --paths . `
    --icon assets/pdf-tools.ico `
    --version-file packaging/version_info.txt `
    --add-data 'assets/pdf-tools.ico;assets' `
    pdf_tools/__main__.py
if ($LASTEXITCODE -ne 0) { throw 'PyInstaller build failed.' }
if (-not (Test-Path 'dist\PDFTools\PDFTools.exe')) { throw 'Missing expected EXE.' }
Write-Host 'Build ready: dist\PDFTools\PDFTools.exe (entire folder required)'
