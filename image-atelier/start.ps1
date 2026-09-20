$ErrorActionPreference = 'Stop'
$env:PYTHONUTF8 = '1'
Set-Location -LiteralPath $PSScriptRoot
$appPython = Join-Path $PSScriptRoot '.venv\Scripts\python.exe'
if (!(Test-Path -LiteralPath $appPython)) { throw 'Run setup.ps1 first.' }
if (!(Test-Path -LiteralPath (Join-Path $PSScriptRoot 'dist\index.html'))) { throw 'Run setup.ps1 first (UI build missing).' }
Write-Host 'Image Atelier: http://127.0.0.1:18791'
Write-Host 'Stop: Ctrl+C. Closing the browser does not cancel an API request.'
$appLogDirectory = Join-Path $PSScriptRoot 'data'
New-Item -ItemType Directory -Path $appLogDirectory -Force | Out-Null
$appStartupLog = Join-Path $appLogDirectory 'startup.log'
Write-Host "Startup log: $appStartupLog"
Start-Transcript -LiteralPath $appStartupLog -Append | Out-Null
try {
    & $appPython server.py
    $appExitCode = $LASTEXITCODE
    if ($appExitCode -ne 0) {
        throw "Image Atelier stopped (exit code $appExitCode). See the preceding Python error and startup log: $appStartupLog"
    }
}
finally {
    Stop-Transcript | Out-Null
}
