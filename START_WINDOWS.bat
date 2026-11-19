@echo off
cd /d "%~dp0"
python -c "import streamlit,yfinance,ddgs,docx,bs4,pypdf" 2>nul || (echo First-time install, a few minutes... & python -m pip install -r requirements.txt)
echo Starting. A browser tab will open. Close this window to stop.
python -m streamlit run app.py
pause
