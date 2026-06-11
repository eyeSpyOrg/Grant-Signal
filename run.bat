@echo off
REM Eye Spy Grant Scout - double-click launcher for Windows
cd /d "%~dp0"
where python >nul 2>nul
if errorlevel 1 (
  echo Python is not installed. Install it from https://www.python.org/downloads/
  echo IMPORTANT: check "Add python.exe to PATH" during install.
  pause
  exit /b 1
)
python -m pip install -q -r requirements.txt
start "" http://127.0.0.1:5000
python app.py
pause
