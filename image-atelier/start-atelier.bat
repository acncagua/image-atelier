@echo off
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0launch-console.ps1" -Target atelier
exit /b %errorlevel%
