@echo off
title Eye Spy Grant Scout
cd /d "%~dp0"

REM Running from inside an un-extracted ZIP breaks everything - catch it early
echo %~dp0 | findstr /i /c:"AppData\Local\Temp" >nul
if not errorlevel 1 (
  echo.
  echo  It looks like you opened this from inside a ZIP file.
  echo  Please right-click the ZIP, choose "Extract All...", then run
  echo  run.bat from the extracted folder instead.
  echo.
  pause
  exit /b 1
)

REM "python --version" catches both missing Python AND the fake
REM Microsoft Store python.exe that new Windows PCs ship with
python --version >nul 2>nul
if errorlevel 1 (
  echo.
  echo  Python is not installed yet ^(one-time setup, about 2 minutes^):
  echo.
  echo   1. Go to  https://www.python.org/downloads/  and click the
  echo      big yellow "Download Python" button.
  echo   2. Open the downloaded file.
  echo   3. IMPORTANT: on the FIRST screen, tick the checkbox
  echo      "Add python.exe to PATH" ^(bottom of the window^).
  echo   4. Click "Install Now" and wait for it to finish.
  echo   5. Close this window and double-click run.bat again.
  echo.
  start "" https://www.python.org/downloads/
  pause
  exit /b 1
)

echo Checking required components (quick after the first run)...
python -m pip install -q -r requirements.txt
if errorlevel 1 (
  echo.
  echo  Something went wrong installing components. Are you connected
  echo  to the internet? Try again, or ask for help with this window open.
  pause
  exit /b 1
)

echo.
echo  Starting Eye Spy Grant Scout...
echo  Your browser will open in a few seconds.
echo.
echo  KEEP THIS BLACK WINDOW OPEN while you use the app.
echo  To stop the app, just close this window.
echo.
REM open the browser after a short delay so the server is up first
start "" /min cmd /c "timeout /t 3 /nobreak >nul & start "" http://127.0.0.1:5000"
python app.py
pause
