@echo off
cd /d "%~dp0"
echo Installing browser mode (about 150 MB, one time)...
python -m pip install playwright
python -m playwright install chromium
echo Done. Now use START_WINDOWS.bat as usual.
pause
