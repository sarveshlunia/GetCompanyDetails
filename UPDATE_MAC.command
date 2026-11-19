#!/bin/bash
cd "$(dirname "$0")"
git pull origin claude/magical-bohr-541l8d
python3 -m pip install -r requirements.txt
echo "Updated. Now double-click START_MAC.command"
