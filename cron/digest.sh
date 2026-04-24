#!/bin/bash
# Cron entry (10h chaque matin) :
#   0 10 * * * /path/to/des_nouvelles_des_etoiles/cron/digest.sh >> /var/log/digest.log 2>&1

set -e

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VENV="$REPO_DIR/.venv"

# Activate virtual environment
cd "$REPO_DIR/src"
"$VENV/bin/python" main.py
