#!/bin/bash
#
# Double-click this on a Mac.
#
# A .command file is what macOS opens in Terminal when double-clicked,
# which is why this exists next to copy-now.sh rather than instead of it:
# same drive, two front doors, one for each kind of machine.
#
# It works on Linux too if you double-click it, so there is only one file
# to explain to anybody.

set -uo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$HERE"

BOLD=$'\033[1m'; DIM=$'\033[2m'; RED=$'\033[31m'
GREEN=$'\033[32m'; YELLOW=$'\033[33m'; OFF=$'\033[0m'

hold() {
    echo
    echo "${DIM}Press Enter to close this window.${OFF}"
    read -r _ || true
}

clear 2>/dev/null
echo
echo "  ${BOLD}HonestBackup — office drive${OFF}"
echo "  ${DIM}$HERE${OFF}"
echo

# ---------------------------------------------------------------------------
# Python is the one thing that has to be on the machine. macOS ships it;
# what it does not ship is the Command Line Tools that provide it, and the
# first run of python3 triggers a dialogue asking to install them. Say so
# plainly rather than letting that dialogue appear out of nowhere.
# ---------------------------------------------------------------------------
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
        echo "  On a Mac, run this once in Terminal and accept the dialogue:"
        echo "      ${BOLD}xcode-select --install${OFF}"
        echo
        echo "  ${DIM}It installs Apple's own command line tools, which"
        echo "  include Python. Nothing else on this drive needs installing.${OFF}"
    else
        echo "      ${BOLD}sudo apt install python3${OFF}"
    fi
    hold; exit 1
fi

# ---------------------------------------------------------------------------
# Everything else travels on the drive. If it has not been fetched yet,
# offer to do it now rather than failing with a list of missing things.
# ---------------------------------------------------------------------------
# A drive prepared for several machines tags each binary by platform, so
# look for this machine's before the untagged copy — the untagged one may
# well belong to whatever machine filled the drive.
case "$(uname -s)" in Darwin) TAG_OS=darwin ;; *) TAG_OS=linux ;; esac
case "$(uname -m)" in arm64|aarch64) TAG_ARCH=arm64 ;; *) TAG_ARCH=amd64 ;; esac
TAG="$TAG_OS-$TAG_ARCH"

missing=""
for needed in rclone age; do
    if [[ ! -x "$HERE/tools/$needed-$TAG" ]] \
       && [[ ! -x "$HERE/tools/$needed" ]] \
       && ! command -v "$needed" >/dev/null 2>&1; then
        missing="$missing $needed"
    fi
done

if [[ -n "$missing" ]]; then
    echo "  ${YELLOW}This drive is missing:${OFF}$missing"
    echo "  ${DIM}They can be downloaded onto the drive itself — nothing"
    echo "  gets installed on this Mac and no password is needed.${OFF}"
    echo
    read -r -p "  Fetch them now? [Y/n] " reply
    case "${reply:-y}" in
        [Nn]*) echo "  ${DIM}Left alone.${OFF}"; hold; exit 1 ;;
        *) bash "$HERE/get-tools.sh" || { hold; exit 1; } ;;
    esac
fi

# ---------------------------------------------------------------------------
# the menu
# ---------------------------------------------------------------------------
while true; do
    echo
    echo "  ${BOLD}What would you like to do?${OFF}"
    echo
    echo "    1.  Copy the latest backups down from the cloud"
    echo "    2.  Look inside the backups already on this drive"
    echo "    3.  Check the drive without touching the network"
    echo "    4.  Re-check every archive against its checksum"
    echo "    q.  Quit"
    echo
    read -r -p "  Choose: " choice
    echo
    case "$choice" in
        1) "$PY" "$HERE/pull.py"; result=$?
           case $result in
               0) echo; echo "  ${GREEN}${BOLD}The drive is up to date.${OFF}" ;;
               2) echo; echo "  ${RED}Finished, but something needs attention above.${OFF}" ;;
               *) echo; echo "  ${RED}Did not finish. Read the message above.${OFF}" ;;
           esac ;;
        2) "$PY" "$HERE/view.py" ;;
        3) "$PY" "$HERE/pull.py" --status ;;
        4) "$PY" "$HERE/pull.py" --verify-all ;;
        q|Q|"") echo "  ${DIM}Nothing was changed.${OFF}"; echo; exit 0 ;;
        *) echo "  ${RED}Pick 1, 2, 3, 4 or q.${OFF}" ;;
    esac
done
