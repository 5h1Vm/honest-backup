#!/usr/bin/env bash
# HonestBackup — office copy. Run by hand, or from cron.
set -uo pipefail
cd "$(dirname "$0")"

exec python3 pull.py "$@"
