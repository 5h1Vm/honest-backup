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
GREEN=$'\033[32m'; YELLOW=$'\033[33m'; CYAN=$'\033[36m'; OFF=$'\033[0m'

hold() {
    echo
    echo "${DIM}Press Enter to close this window.${OFF}"
    read -r _ || true
}

banner() {
    clear 2>/dev/null
    echo
    echo "  ${BOLD}${CYAN}◆ HonestBackup${OFF}"
    echo "  ${DIM}$HERE${OFF}"
}

banner

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
    echo
    echo "  ${RED}Python 3 is not available on this machine.${OFF}"
    echo
    if [[ "$(uname -s)" == "Darwin" ]]; then
        echo "  Run once in Terminal, accept the dialogue:"
        echo "      ${BOLD}xcode-select --install${OFF}"
        echo
        echo "  ${DIM}Installs Apple's own tools, which include Python.${OFF}"
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
    echo
    echo "  ${YELLOW}Missing:${OFF}$missing"
    echo "  ${DIM}Fetched onto the drive — nothing installed here.${OFF}"
    echo
    read -r -p "  Fetch now? [Y/n] " reply
    case "${reply:-y}" in
        [Nn]*) echo "  ${DIM}Left alone.${OFF}"; hold; exit 1 ;;
        *) bash "$HERE/get-tools.sh" || { hold; exit 1; } ;;
    esac
fi

# ---------------------------------------------------------------------------
# the private key for this session
#
# It never lives on the drive — a lost drive would then be a readable
# copy of everything. Entering it here holds it only in this Terminal
# window's own memory, for Browse to reuse without asking again.
# ---------------------------------------------------------------------------
enter_key() {
    echo
    echo "  ${DIM}Starts with AGE-SECRET-KEY-1. Input is hidden, nothing saved.${OFF}"
    read -rs -p "  Key: " typed
    echo
    if [[ -z "$typed" ]]; then
        echo "  ${DIM}Nothing entered.${OFF}"
        return
    fi
    export HONESTBACKUP_KEY_TEXT="$typed"
    unset typed
    echo "  ${GREEN}Set for this session.${OFF}"
}

# ---------------------------------------------------------------------------
# the menu
# ---------------------------------------------------------------------------
while true; do
    echo
    echo "  ${BOLD}1${OFF}) Sync      ${BOLD}2${OFF}) Browse    ${BOLD}3${OFF}) Reports"
    echo "  ${BOLD}4${OFF}) Status    ${BOLD}5${OFF}) Verify    ${BOLD}6${OFF}) Key"
    echo "  ${BOLD}q${OFF}) Quit"
    echo
    read -r -p "  ${CYAN}›${OFF} " choice
    echo
    case "$choice" in
        1) "$PY" "$HERE/pull.py"; result=$?
           case $result in
               0) echo; echo "  ${GREEN}${BOLD}Up to date.${OFF}" ;;
               2) echo; echo "  ${RED}Needs attention — see above.${OFF}" ;;
               *) echo; echo "  ${RED}Did not finish — see above.${OFF}" ;;
           esac ;;
        2) "$PY" "$HERE/view.py" ;;
        3) "$PY" "$HERE/view.py" --reports-menu ;;
        4) "$PY" "$HERE/pull.py" --status ;;
        5) "$PY" "$HERE/pull.py" --verify-all ;;
        6) enter_key ;;
        q|Q|"") echo "  ${DIM}Nothing changed.${OFF}"; echo; exit 0 ;;
        *) echo "  ${RED}Pick 1–6 or q.${OFF}" ;;
    esac
done
