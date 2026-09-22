param(
    [string]$PortableRoot = 'K:\ComfyUI-Atelier',
    [int]$Port = 8188
)
$ErrorActionPreference = 'Stop'
$comfyPython = Join-Path $PortableRoot 'python_embeded\python.exe'
$comfyMain = Join-Path $PortableRoot 'ComfyUI\main.py'
if (!(Test-Path -LiteralPath $comfyPython) -or !(Test-Path -LiteralPath $comfyMain)) {
    throw 'Extract the current official NVIDIA portable build first. See docs/COMFYUI_SETUP.md. Pass -PortableRoot with the directory containing python_embeded and ComfyUI.'
}
if ($Port -lt 1024 -or $Port -gt 65535) { throw 'Port must be between 1024 and 65535.' }
Push-Location -LiteralPath $PortableRoot
try {
    & $comfyPython -s $comfyMain --windows-standalone-build --listen 127.0.0.1 --port $Port --cache-none
    if ($LASTEXITCODE -ne 0) { throw "ComfyUI stopped with exit code $LASTEXITCODE. Check the preceding error." }
} finally { Pop-Location }
