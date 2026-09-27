param(
    [string]$PortableRoot = 'K:\ComfyUI-Atelier',
    [int]$Port = 8188
)
$ErrorActionPreference = 'Stop'
$env:PYTHONUTF8 = '1'
$comfyPython = Join-Path $PortableRoot 'python_embeded\python.exe'
$comfyMain = Join-Path $PortableRoot 'ComfyUI\main.py'
if (!(Test-Path -LiteralPath $comfyPython) -or !(Test-Path -LiteralPath $comfyMain)) {
    throw 'Extract the current official NVIDIA portable build first. See docs/COMFYUI_SETUP.md. Pass -PortableRoot with the directory containing python_embeded and ComfyUI.'
}
if ($Port -lt 1024 -or $Port -gt 65535) { throw 'Port must be between 1024 and 65535.' }
Push-Location -LiteralPath $PortableRoot
$transcribing = $false
try {
    $logDirectory = Join-Path $PSScriptRoot 'data'
    New-Item -ItemType Directory -Path $logDirectory -Force | Out-Null
    Start-Transcript -LiteralPath (Join-Path $logDirectory 'comfy-startup.log') -Append | Out-Null
    $transcribing = $true
    & $comfyPython -u -s $comfyMain --windows-standalone-build --listen 127.0.0.1 --port $Port --disable-auto-launch
    if ($LASTEXITCODE -ne 0) { throw "ComfyUI stopped with exit code $LASTEXITCODE. Check the preceding error." }
} finally {
    if ($transcribing) { Stop-Transcript | Out-Null }
    Pop-Location
}
