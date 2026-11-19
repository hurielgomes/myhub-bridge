@echo off
chcp 65001 >nul
cd /d "%~dp0"
where py >nul 2>nul
if errorlevel 1 (echo Instale Python 3.10+ e marque Add Python to PATH.& pause& exit /b 1)
if not exist ".venv\Scripts\python.exe" py -3 -m venv .venv
if errorlevel 1 goto erro
".venv\Scripts\python.exe" -m pip install --upgrade pip
if errorlevel 1 goto erro
".venv\Scripts\python.exe" -m pip install -r requirements.txt
if errorlevel 1 goto erro
".venv\Scripts\python.exe" -m playwright install chromium
if errorlevel 1 goto erro
echo Instalacao concluida. Execute login.bat.
pause
exit /b 0
:erro
echo Falha na instalacao. Confira a mensagem acima.
pause
exit /b 1
