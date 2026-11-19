@echo off
cd /d "%~dp0"
echo Installing (first time takes a few minutes)...
python -m pip install -r requirements.txt
echo Starting the tool. A browser tab will open. Close this window to stop.
python -m streamlit run app.py
pause
