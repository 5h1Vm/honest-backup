#!/bin/bash
#
# The fast one: copy backups down and quit. No menu, no questions.
#
# HonestBackup.command is for someone deciding what to do. This is for
# someone who already knows — plug the drive in, double-click Sync, walk
# away. It is the Mac twin of copy-now.sh, which does the same job on
# Linux.

set -uo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$HERE"

BOLD=$'\033[1m'; DIM=$'\033[2m'; RED=$'\033[31m'
GREEN=$'\033[32m'; OFF=$'\033[0m'

hold() {
    echo
    echo "${DIM}Press Enter to close this window.${OFF}"
    read -r _ || true
}

clear 2>/dev/null
echo
echo "  ${BOLD}HonestBackup — sync${OFF}"
echo "  ${DIM}Backblaze  ->  this drive.  Nothing is ever deleted from the drive.${OFF}"
echo

PY=""
for candidate in python3 python; do
    if command -v "$candidate" >/dev/null 2>&1; then
        if "$candidate" -c 'import sys; sys.exit(0 if sys.version_info>=(3,8) else 1)' 2>/dev/null; then
            PY="$candidate"; break
        fi
    fi
done

if [[ -z "$PY" ]]; then
    echo "  ${RED}Python 3 is not available on this machine.${OFF}"
    echo
    if [[ "$(uname -s)" == "Darwin" ]]; then
        echo "  Run this once in Terminal and accept the dialogue:"
        echo "      ${BOLD}xcode-select --install${OFF}"
    else
        echo "      ${BOLD}sudo apt install python3${OFF}"
    fi
    hold; exit 1
fi

if [[ ! -x "$HERE/tools/rclone" ]] && ! command -v rclone >/dev/null 2>&1; then
    echo "  ${RED}This drive has not been set up on this machine yet.${OFF}"
    echo "  ${DIM}Open ${BOLD}Menu Mac${OFF}${DIM} at the top of the drive once — it can fetch"
    echo "  what is needed, and this shortcut works after that.${OFF}"
    hold; exit 1
fi

"$PY" "$HERE/pull.py"
RESULT=$?

echo
case $RESULT in
    0) echo "  ${GREEN}${BOLD}Finished. The drive is up to date.${OFF}" ;;
    2) echo "  ${RED}${BOLD}Finished, but something needs attention above.${OFF}"
       echo "  ${DIM}A failed checksum usually means a damaged file. Running this"
       echo "  again often repairs it from the cloud copy.${OFF}" ;;
    *) echo "  ${RED}${BOLD}Did not finish. Read the message above.${OFF}" ;;
esac

hold
exit $RESULT
