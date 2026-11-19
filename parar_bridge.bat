@echo off
chcp 65001 >nul
cd /d "%~dp0"
if exist ".venv\Scripts\python.exe" (
  ".venv\Scripts\python.exe" main.py --shutdown
) else (
  py main.py --shutdown
)
echo.
pause
