#!/usr/bin/env bash
#
# Get HonestBackup ready to run on a fresh machine.
#
# Installs the system tools, the Python packages, the browser Notion's
# export needs, and creates config/backup.conf from the example. Safe to
# run again: everything is checked before it is installed.
#
#   ./setup.sh            install everything
#   ./setup.sh --check    report what is missing, install nothing

set -uo pipefail
cd "$(dirname "$0")"

CHECK_ONLY=false
[[ "${1:-}" == "--check" ]] && CHECK_ONLY=true

BOLD=$'\033[1m'; DIM=$'\033[2m'; RED=$'\033[31m'
GREEN=$'\033[32m'; YELLOW=$'\033[33m'; OFF=$'\033[0m'

ok()   { echo "  ${GREEN}✓${OFF} $*"; }
warn() { echo "  ${YELLOW}!${OFF} $*"; }
bad()  { echo "  ${RED}✗${OFF} $*"; }
step() { echo; echo "${BOLD}$*${OFF}"; }

MISSING=()

# The tools that must exist for a backup to complete, and what installs them.
declare -A TOOLS=(
    [python3]="python3"
    [pip3]="python3-pip"
    [age]="age"
    [age-keygen]="age"
    [zstd]="zstd"
    [tar]="tar"
    [rclone]="rclone"
    [keepassxc-cli]="keepassxc"
    [crontab]="cron"
)

step "Checking what is already here"
for tool in "${!TOOLS[@]}"; do
    if command -v "$tool" >/dev/null 2>&1; then
        ok "$tool"
    else
        bad "$tool  ${DIM}(from ${TOOLS[$tool]})${OFF}"
        MISSING+=("${TOOLS[$tool]}")
    fi
done

# ---------------------------------------------------------------------------
# system packages
# ---------------------------------------------------------------------------
if [[ ${#MISSING[@]} -gt 0 ]]; then
    # Deduplicate: age and age-keygen come from one package.
    PACKAGES=$(printf '%s\n' "${MISSING[@]}" | sort -u | tr '\n' ' ')
    if $CHECK_ONLY; then
        step "Would install"
        echo "  sudo apt-get install -y $PACKAGES"
    else
        step "Installing system tools"
        echo "  ${DIM}$PACKAGES${OFF}"
        if ! command -v apt-get >/dev/null 2>&1; then
            bad "This installs with apt-get, which is not on this machine."
            echo "     Install these yourself, then run setup again: $PACKAGES"
            exit 1
        fi
        sudo apt-get update -qq \
            && sudo apt-get install -y -qq $PACKAGES \
            || { bad "Could not install: $PACKAGES"; exit 1; }
        ok "installed"
    fi
fi

$CHECK_ONLY || step "Installing Python packages"
if ! $CHECK_ONLY; then
    # --break-system-packages is needed on Debian/Ubuntu 23.04+ where pip
    # refuses to touch a system Python without it. Harmless on older ones.
    pip3 install --user --quiet -r requirements.txt 2>/dev/null \
        || pip3 install --user --quiet --break-system-packages -r requirements.txt \
        || { bad "pip install failed — try a virtualenv"; exit 1; }
    ok "installed from requirements.txt"

    step "Installing the browser Notion's export needs"
    if python3 -m playwright install chromium >/dev/null 2>&1; then
        ok "chromium ready"
    else
        warn "could not install chromium — Notion's export will not run."
        warn "try: python3 -m playwright install --with-deps chromium"
    fi
fi

# ---------------------------------------------------------------------------
# folders and configuration
# ---------------------------------------------------------------------------
if ! $CHECK_ONLY; then
    step "Creating folders"
    mkdir -p workspace logs state config/keys backupvault/{archives,hashes,manifests,reports,metadata}
    chmod 700 config/keys
    ok "workspace, logs, state, config/keys, backupvault"

    step "Configuration"
    if [[ -f config/backup.conf ]]; then
        ok "config/backup.conf already exists, left alone"
    else
        cp config/backup.conf.example config/backup.conf
        # Point Notion at whichever chromium Playwright just unpacked.
        BROWSER=$(python3 - <<'PY' 2>/dev/null
import glob, os
found = glob.glob(os.path.expanduser(
    "~/.cache/ms-playwright/chromium-*/chrome-linux*/chrome"))
print(found[0] if found else "")
PY
)
        if [[ -n "$BROWSER" ]]; then
            sed -i "s|^NOTION_BROWSER_EXECUTABLE=.*|NOTION_BROWSER_EXECUTABLE=$BROWSER|" config/backup.conf
            ok "created, Notion browser set to the bundled chromium"
        else
            warn "created, but no chromium found — set NOTION_BROWSER_EXECUTABLE by hand"
        fi
    fi

    if [[ -f .env ]]; then
        ok ".env already exists, left alone"
    else
        cp .env.example .env
        chmod 600 .env
        warn ".env created from the example — put your real values in it"
    fi
fi

# ---------------------------------------------------------------------------
# what is left for a person to do
# ---------------------------------------------------------------------------
step "Where things stand"
STILL=()
[[ -f .env ]] && grep -q "your-master-password" .env 2>/dev/null \
    && STILL+=("Edit .env — it still has the example password in it")
[[ -f secrets.kdbx ]] || STILL+=("No credential database yet — the setup wizard creates one")
command -v rclone >/dev/null && ! rclone listremotes 2>/dev/null | grep -q . \
    && STILL+=("No rclone remote configured — run: rclone config")

if [[ ${#STILL[@]} -eq 0 ]]; then
    ok "Everything is in place."
else
    for item in "${STILL[@]}"; do warn "$item"; done
fi

echo
echo "${BOLD}Next${OFF}"
echo "  python3 -m orchestrator.run --tui       ${DIM}open the interface${OFF}"
echo "  ${DIM}then choose First-time setup, which walks through credentials,${OFF}"
echo "  ${DIM}storage and the schedule.${OFF}"
echo
