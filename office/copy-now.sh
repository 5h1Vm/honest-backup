#!/usr/bin/env bash
#
# Double-click this to copy the backups down onto the office drive.
#
# It is deliberately chatty and it waits for a keypress at the end, because
# it is meant to be run by a person who wants to see that it worked — not
# by a machine. For the unattended version, see install-schedule.sh.
#
# Direction is one-way: Backblaze -> the drive. It never writes to
# Backblaze and never deletes anything from the drive.

set -uo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$HERE"

BOLD=$'\033[1m'; DIM=$'\033[2m'; RED=$'\033[31m'; GREEN=$'\033[32m'; OFF=$'\033[0m'

hold() {
    echo
    echo "${DIM}Press Enter to close this window.${OFF}"
    read -r _ || true
}

echo
echo "${BOLD}HonestBackup — copy to the office drive${OFF}"
echo "${DIM}Backblaze  ->  this drive.  Nothing is ever deleted from the drive.${OFF}"
echo

# ---------------------------------------------------------------------------
# the things people actually get wrong, checked in the order they hit them
# ---------------------------------------------------------------------------
if ! command -v python3 >/dev/null 2>&1; then
    echo "${RED}Python 3 is not installed.${OFF}"
    echo "  Install it with:  sudo apt install python3"
    hold; exit 1
fi

if ! command -v rclone >/dev/null 2>&1; then
    echo "${RED}rclone is not installed.${OFF}"
    echo "  Install it with:  sudo apt install rclone"
    hold; exit 1
fi

if [[ ! -f "$HERE/pull.conf" ]]; then
    echo "${RED}This has not been set up yet.${OFF}"
    echo "  Copy pull.conf.example to pull.conf and put the drive's path in it."
    echo "  The README next to this file walks through it."
    hold; exit 1
fi

DESTINATION="$(grep -E '^[[:space:]]*DESTINATION[[:space:]]*=' "$HERE/pull.conf" \
    | head -1 | cut -d= -f2- | xargs)"

if [[ -n "$DESTINATION" && ! -d "$DESTINATION" ]]; then
    echo "${RED}The drive is not plugged in.${OFF}"
    echo "  Looked for: $DESTINATION"
    echo
    echo "  Plug it in and run this again. Nothing was changed."
    hold; exit 1
fi

# ---------------------------------------------------------------------------
# do the work
# ---------------------------------------------------------------------------
python3 "$HERE/pull.py"
RESULT=$?

echo
case $RESULT in
    0) echo "${GREEN}${BOLD}Finished. The office drive is up to date.${OFF}" ;;
    2) echo "${RED}${BOLD}Finished, but something needs attention — read the message above.${OFF}"
       echo "${DIM}A failed checksum usually means a damaged file. Running this again"
       echo "often repairs it from the cloud copy.${OFF}" ;;
    *) echo "${RED}${BOLD}Did not finish. Read the message above.${OFF}" ;;
esac

hold
exit $RESULT
