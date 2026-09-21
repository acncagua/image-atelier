param([string]$ModelPath = 'K:\Qwen-Image-2.1')
$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
$env:PYTHONUTF8 = '1'
$qwenPython = Join-Path $PSScriptRoot '.venv-qwen\Scripts\python.exe'
if (!(Test-Path -LiteralPath $qwenPython)) {
    & (Join-Path $PSScriptRoot '.venv\Scripts\python.exe') -m venv .venv-qwen
    if ($LASTEXITCODE -ne 0) { throw 'Qwen venv creation failed.' }
}
& $qwenPython -m pip install torch==2.7.1 torchvision==0.22.1 --index-url https://download.pytorch.org/whl/cu128
if ($LASTEXITCODE -ne 0) { throw 'Qwen CUDA dependency installation failed.' }
& $qwenPython -m pip install -r requirements-qwen-lock.txt
if ($LASTEXITCODE -ne 0) { throw 'Qwen dependency installation failed.' }
& $qwenPython -m pip check
if ($LASTEXITCODE -ne 0) { throw 'Qwen dependencies are inconsistent.' }
$env:HF_HUB_OFFLINE = '1'
$env:TRANSFORMERS_OFFLINE = '1'
& $qwenPython qwen_probe.py --model $ModelPath --environment --report '.tmp/qwen/preflight.json'
if ($LASTEXITCODE -ne 0) { throw 'Qwen preflight failed; see .tmp/qwen/preflight.json.' }
Write-Host 'Qwen environment ready. No weights loaded and no images generated.'
