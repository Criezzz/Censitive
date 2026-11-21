@echo off
cd /d "%~dp0"
powershell -NoProfile -ExecutionPolicy Bypass -File tool\demo.ps1
pause
