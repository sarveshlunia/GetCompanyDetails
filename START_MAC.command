#!/bin/bash
cd "$(dirname "$0")"
python3 -c "import streamlit,yfinance,ddgs,docx,bs4" 2>/dev/null || python3 -m pip install -r requirements.txt
python3 -m streamlit run app.py
