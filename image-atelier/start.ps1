$ErrorActionPreference = 'Stop'
$env:PYTHONUTF8 = '1'
Set-Location -LiteralPath $PSScriptRoot
$appPython = Join-Path $PSScriptRoot '.venv\Scripts\python.exe'
if (!(Test-Path -LiteralPath $appPython)) { throw 'Run setup.ps1 first.' }
if (!(Test-Path -LiteralPath (Join-Path $PSScriptRoot 'dist\index.html'))) { throw 'Run setup.ps1 first (UI build missing).' }
Write-Host 'Image Atelier: http://127.0.0.1:18791'
Write-Host 'Stop: Ctrl+C. Closing the browser does not cancel an API request.'
& $appPython server.py
if ($LASTEXITCODE -ne 0) { throw 'Server stopped with an error. Check whether another instance is running.' }
