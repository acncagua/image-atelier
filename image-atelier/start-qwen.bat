@echo off
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0launch-console.ps1" -Target qwen -PortableRoot "K:\ComfyUI"
exit /b %errorlevel%
