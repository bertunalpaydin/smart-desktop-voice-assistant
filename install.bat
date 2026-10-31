@echo off
rem One-time setup: installs the packages and puts a Jarvis shortcut on the desktop.
cd /d "%~dp0"
echo Installing what Jarvis needs. The first time this can take a few minutes...
rem --isolated ignores any broken pip settings on this computer
python -m pip install --isolated --index-url https://pypi.org/simple -r requirements.txt
if errorlevel 1 goto failed
python main.py shortcut
if errorlevel 1 goto failed
echo.
echo All set. Start Jarvis with the icon on your desktop.
pause
exit /b 0
:failed
echo.
echo Something went wrong, see the messages above.
pause
exit /b 1
