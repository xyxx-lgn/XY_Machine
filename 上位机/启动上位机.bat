@echo off
setlocal
cd /d "%~dp0"
set "TEMP=%~dp0.runtime\tmp"
set "TMP=%TEMP%"
set "PYTHONDONTWRITEBYTECODE=1"
if not exist "%~dp0.venv\Scripts\pythonw.exe" (
    echo Python environment not found. See README.md for setup.
    pause
    exit /b 1
)
start "" "%~dp0.venv\Scripts\pythonw.exe" -B "%~dp0main.py"
