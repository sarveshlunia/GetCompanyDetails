@echo off
cd /d "%~dp0"
echo Getting the latest version...
git pull origin claude/magical-bohr-541l8d
python -m pip install -r requirements.txt
echo Updated. Now double-click START_WINDOWS.bat
pause
