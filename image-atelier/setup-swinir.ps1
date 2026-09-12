param([string]$ModelPath = '')
$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
python -m venv .venv-swinir
if ($LASTEXITCODE -ne 0) { throw 'Python 3.12 venv creation failed.' }
& '.\.venv-swinir\Scripts\python.exe' -m pip install torch==2.7.1 torchvision==0.22.1 --index-url https://download.pytorch.org/whl/cu128
if ($LASTEXITCODE -ne 0) { throw 'CUDA 12.8 PyTorch installation failed.' }
& '.\.venv-swinir\Scripts\python.exe' -m pip install -r requirements-swinir.txt
if ($LASTEXITCODE -ne 0) { throw 'SwinIR dependency installation failed.' }
if ($ModelPath) {
    $resolvedModel = (Resolve-Path -LiteralPath $ModelPath).Path
    New-Item -ItemType Directory -Force 'data' | Out-Null
    @{python=(Join-Path $PSScriptRoot '.venv-swinir\Scripts\python.exe');model=$resolvedModel} | ConvertTo-Json | Set-Content -LiteralPath 'data\upscale-settings.json' -Encoding utf8
}
Write-Host 'SwinIR environment ready. No model is downloaded. Configure your local model in the app.'
