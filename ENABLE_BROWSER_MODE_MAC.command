#!/bin/bash
cd "$(dirname "$0")"
python3 -m pip install playwright
python3 -m playwright install chromium
echo Done. Now use START_MAC.command as usual.
