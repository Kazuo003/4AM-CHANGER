$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
python -m unittest discover -s tests -v
if ($LASTEXITCODE -ne 0) { throw 'Unit tests failed.' }
python app.py --smoke-test
if ($LASTEXITCODE -ne 0) { throw 'GUI smoke test failed.' }
python -m PyInstaller --noconfirm --clean --onedir --windowed --name 4AM-Changer --add-data 'assets;assets' app.py
if ($LASTEXITCODE -ne 0) { throw 'Packaging failed.' }
python package_release.py
if ($LASTEXITCODE -ne 0) { throw 'Full ZIP verification failed.' }
Write-Host "Built: $PSScriptRoot\dist\4AM-Changer\4AM-Changer.exe"
Write-Host "Full package: $PSScriptRoot\packages"
