#!/bin/sh
# Try Hindsight in your normal browser (no emulator needed).
# Data is kept in ./.hindsight-data; delete that folder to start fresh.
cd "$(dirname "$0")/.." || exit 1
echo "Open http://127.0.0.1:7070 in your browser. Ctrl+C to stop."
exec python3 -m hindsightd --dev "$@"
