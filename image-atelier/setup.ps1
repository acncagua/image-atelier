$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
python -m venv .venv
if ($LASTEXITCODE -ne 0) { throw 'Python venv setup failed. Python 3.12 is required.' }
& '.\.venv\Scripts\python.exe' -m pip install -r requirements.txt
if ($LASTEXITCODE -ne 0) { throw 'Python dependency installation failed.' }
npm.cmd ci
if ($LASTEXITCODE -ne 0) { throw 'npm ci failed. Node.js 22.12+ is required.' }
npm.cmd run build
if ($LASTEXITCODE -ne 0) { throw 'UI build failed.' }
Write-Host 'Ready. Run start.cmd and open http://127.0.0.1:18791'
