#!/usr/bin/env bash
#
# Bring HonestBackup up on a fresh server, start to finish.
#
# This is what you run once, on the machine that will hold the backups.
# It installs what is missing, generates this deployment's own encryption
# key, stores its credentials, points it at its bucket, writes its
# configuration — and then proves each piece works before saying it is done.
#
#   ./first-run.sh                 answer the questions as they come
#   ./first-run.sh --answers FILE  read the answers from a file instead
#   ./first-run.sh --check         test what is already here, change nothing
#   ./first-run.sh --answers-file  print a file to fill in beforehand
#
# How this relates to First-time setup in the TUI
#   They are the same installation, writing the same files — .env,
#   config/backup.conf, config/keys/archive.key, secrets.kdbx — so whatever
#   one does, the other sees.
#
#   The TUI is better at everything it covers: turning services on and off,
#   editing a credential, the schedule, generating a key. But it cannot get
#   a bare server to the point where it can run at all. There is no
#   credential database for it to open yet and no rclone remote to copy
#   through, and it has no way to create either. So it opens onto a wizard
#   pointing at a database that is not there.
#
#   This script exists for that first hour only. It creates the database,
#   the key and the remote, writes a working configuration, proves each
#   piece answers, and then hands over to the TUI for everything after.
#
# Every installation is self-contained: its own key, its own credentials,
# its own bucket, its own schedule. Nothing here reaches another
# installation and nothing is shared between them, which is what makes it
# safe to hand the whole thing to somebody and walk away.
#
# Nothing is ever overwritten. If a key or a credential database is already
# present this stops and says so, because replacing an encryption key makes
# every archive taken with the old one unreadable.
#
# Answers are collected before anything is written, so a half-finished
# install is not left behind when someone gets three questions in and
# realises they do not have the Cloudflare token yet.

set -uo pipefail
cd "$(dirname "$0")"
ROOT="$PWD"

BOLD=$'\033[1m'; DIM=$'\033[2m'; RED=$'\033[31m'
GREEN=$'\033[32m'; YELLOW=$'\033[33m'; CYAN=$'\033[36m'; OFF=$'\033[0m'

ok()   { echo "  ${GREEN}✓${OFF} $*"; }
warn() { echo "  ${YELLOW}!${OFF} $*"; }
bad()  { echo "  ${RED}✗${OFF} $*"; }
step() { echo; echo "${BOLD}$*${OFF}"; }
die()  { echo; echo "  ${RED}Stopped:${OFF} $*"; echo; exit 1; }

MODE=install
ANSWERS=""
while [[ $# -gt 0 ]]; do
    case "$1" in
        --check)        MODE=check ;;
        --answers-file) MODE=template ;;
        --answers)      MODE=file; ANSWERS="${2:-}"; shift ;;
        --answers=*)    MODE=file; ANSWERS="${1#*=}" ;;
        -h|--help)      sed -n '2,26p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
        *)              die "I do not know the option $1" ;;
    esac
    shift
done

# ---------------------------------------------------------------------------
# a file someone can fill in before sitting down at the server
# ---------------------------------------------------------------------------
if [[ "$MODE" == template ]]; then
cat <<'ANSWERS'
# HonestBackup — answers for a fresh install.
#   ./first-run.sh --answers this-file
#
# Fill this in beforehand, ideally somewhere you already keep secrets, and
# delete it once the install is done. Every value ends up either in the
# encrypted credential database or in config/backup.conf.

ORGANISATION="Example Ltd"

# --- Microsoft 365 -------------------------------------------------------
# An Entra app registration with application permissions, admin-consented.
# SharePoint file contents additionally need a certificate uploaded to that
# registration — Graph will not accept a client secret for them.
TENANT_ID=""
CLIENT_ID=""
CLIENT_SECRET=""

# --- Cloudflare ----------------------------------------------------------
CLOUDFLARE_API_TOKEN=""
CLOUDFLARE_ACCOUNT_ID=""
ZONE_ID=""

# --- Notion --------------------------------------------------------------
NOTION_TOKEN=""

# --- Backblaze B2 --------------------------------------------------------
# Scope this key to this bucket alone. A key that can reach every bucket in
# the account turns one lost laptop into a much larger problem.
B2_KEY_ID=""
B2_APPLICATION_KEY=""
B2_BUCKET=""

# --- Where the reports go ------------------------------------------------
EMAIL_FROM="HonestBackup <backups@example.com>"
EMAIL_TO="someone@example.com"
EMAIL_USERNAME=""
EMAIL_PASSWORD=""            # an app password, not the account password
TELEGRAM_BOT_TOKEN=""        # blank to skip Telegram entirely
TELEGRAM_CHAT_ID=""

# --- How long each destination keeps archives ---------------------------
LOCAL_RETENTION_DAYS=14
BACKBLAZE_RETENTION_DAYS=1095
USB_RETENTION_DAYS=forever

# --- The master password for this install's credential database ---------
KEEPASS_PASSWORD=""
ANSWERS
exit 0
fi

echo
echo "  ${BOLD}${CYAN}◆ HonestBackup — first run${OFF}"
echo "  ${DIM}$ROOT${OFF}"

# ---------------------------------------------------------------------------
# 1. the machine
# ---------------------------------------------------------------------------
step "1. Machine"
NEEDED=(python3 pip3 age age-keygen zstd tar rclone keepassxc-cli crontab)
MISSING=()
for tool in "${NEEDED[@]}"; do
    command -v "$tool" >/dev/null 2>&1 && ok "$tool" || { bad "$tool"; MISSING+=("$tool"); }
done

if [[ ${#MISSING[@]} -gt 0 ]]; then
    if [[ "$MODE" == check ]]; then
        die "Missing: ${MISSING[*]} — run ./setup.sh"
    fi
    echo
    read -r -p "  Install the missing tools now? [Y/n] " reply
    case "${reply:-y}" in
        [Nn]*) die "Run ./setup.sh yourself, then come back." ;;
        *) ./setup.sh || die "setup.sh did not finish" ;;
    esac
fi

# ---------------------------------------------------------------------------
# 2. the answers
# ---------------------------------------------------------------------------
declare -A A

if [[ "$MODE" == file ]]; then
    step "2. Answers"
    [[ -f "$ANSWERS" ]] || die "No such file: $ANSWERS"
    while IFS= read -r line; do
        line="${line%%#*}"; line="${line#"${line%%[![:space:]]*}"}"
        [[ -z "$line" || "$line" != *=* ]] && continue
        k="${line%%=*}"; v="${line#*=}"
        k="${k// /}"
        # Trim before unquoting, and again after. A value left blank as
        #     EMAIL_PASSWORD=""      # an app password
        # keeps the spaces the comment used to sit behind, so the closing
        # quote is no longer last — unquoting then strips only the opening
        # one and a credential nobody entered is stored as the string '"'.
        trim() { local s="$1"; s="${s#"${s%%[![:space:]]*}"}"; printf '%s' "${s%"${s##*[![:space:]]}"}"; }
        v="$(trim "$v")"
        v="${v#\"}"; v="${v%\"}"; v="${v#\'}"; v="${v%\'}"
        v="$(trim "$v")"
        A["$k"]="$v"
    done < "$ANSWERS"
    ok "read $(basename "$ANSWERS")"
elif [[ "$MODE" == install ]]; then
    ask() {
        local key="$1" prompt="$2" secret="${3:-}" reply
        [[ -n "${A[$key]:-}" ]] && return 0
        if [[ -n "$secret" ]]; then read -rs -p "    $prompt: " reply; echo
        else read -r -p "    $prompt: " reply; fi
        A["$key"]="$reply"
    }
    step "2. Answers"
    echo "  ${DIM}Nothing is written until all of them are in — leave a section"
    echo "  blank to skip that service entirely.${OFF}"
    echo
    ask ORGANISATION "Organisation name"
    echo; echo "  ${DIM}Microsoft 365${OFF}"
    ask TENANT_ID     "Tenant ID"
    ask CLIENT_ID     "Client ID"
    ask CLIENT_SECRET "Client secret" secret
    echo; echo "  ${DIM}Cloudflare${OFF}"
    ask CLOUDFLARE_API_TOKEN  "API token" secret
    ask CLOUDFLARE_ACCOUNT_ID "Account ID"
    ask ZONE_ID               "Zone ID"
    echo; echo "  ${DIM}Notion${OFF}"
    ask NOTION_TOKEN "Integration token" secret
    echo; echo "  ${DIM}Backblaze B2${OFF}"
    ask B2_KEY_ID          "Key ID"
    ask B2_APPLICATION_KEY "Application key" secret
    ask B2_BUCKET          "Bucket name"
    echo; echo "  ${DIM}Where the reports go${OFF}"
    ask EMAIL_FROM         "From address"
    ask EMAIL_TO           "To (comma separated)"
    ask EMAIL_USERNAME     "SMTP username"
    ask EMAIL_PASSWORD     "SMTP app password" secret
    ask TELEGRAM_BOT_TOKEN "Telegram bot token (blank to skip)" secret
    ask TELEGRAM_CHAT_ID   "Telegram chat ids (blank to skip)"
    echo; echo "  ${DIM}A master password for this install's credential database${OFF}"
    ask KEEPASS_PASSWORD "Master password" secret
fi

: "${A[LOCAL_RETENTION_DAYS]:=14}"
: "${A[BACKBLAZE_RETENTION_DAYS]:=1095}"
: "${A[USB_RETENTION_DAYS]:=forever}"

# ---------------------------------------------------------------------------
# 3. this installation's encryption key
# ---------------------------------------------------------------------------
KEY="config/keys/archive.key"
PUB="config/keys/archive.pub"

if [[ "$MODE" != check ]]; then
    step "3. Encryption key"
    mkdir -p config/keys && chmod 700 config/keys
    if [[ -f "$KEY" ]]; then
        warn "$KEY already exists — left alone"
        warn "replacing it would make every existing archive unreadable"
    else
        age-keygen -o "$KEY" 2>/dev/null || die "age-keygen failed"
        chmod 600 "$KEY"
        age-keygen -y "$KEY" > "$PUB" 2>/dev/null
        ok "generated — public key in $PUB"
        echo
        echo "  ${RED}${BOLD}Copy $KEY somewhere offline before going further.${OFF}"
        echo "  ${DIM}It is the only thing that opens these archives. It is not in"
        echo "  the bucket, not on the drive, and not recoverable from either.${OFF}"
    fi
fi

# ---------------------------------------------------------------------------
# 4. credentials
# ---------------------------------------------------------------------------
DB="secrets.kdbx"
SECRET_KEYS=(TENANT_ID CLIENT_ID CLIENT_SECRET CLOUDFLARE_API_TOKEN ZONE_ID
             CLOUDFLARE_ACCOUNT_ID NOTION_TOKEN B2_KEY_ID B2_APPLICATION_KEY
             EMAIL_USERNAME EMAIL_PASSWORD TELEGRAM_BOT_TOKEN)

if [[ "$MODE" != check ]]; then
    step "4. Credentials"
    if [[ -f "$DB" ]]; then
        warn "$DB already exists — left alone"
    else
        [[ -n "${A[KEEPASS_PASSWORD]:-}" ]] || die "No master password given."
        printf '%s\n%s\n' "${A[KEEPASS_PASSWORD]}" "${A[KEEPASS_PASSWORD]}" \
            | keepassxc-cli db-create -p "$DB" >/dev/null 2>&1 \
            || die "could not create $DB"
        chmod 600 "$DB"
        ok "created $DB"
        for k in "${SECRET_KEYS[@]}"; do
            v="${A[$k]:-}"
            [[ -z "$v" ]] && { warn "$k not given — skipped"; continue; }
            printf '%s\n%s\n' "${A[KEEPASS_PASSWORD]}" "$v" \
                | keepassxc-cli add -p --quiet "$DB" "$k" >/dev/null 2>&1 \
                && ok "$k" || bad "$k could not be stored"
        done
    fi

    if [[ -f .env ]]; then
        warn ".env already exists — left alone"
    else
        {
            echo "# How to open the credential database. No secrets of its own."
            echo "KEEPASS_DATABASE=$ROOT/$DB"
            echo "KEEPASS_PASSWORD=${A[KEEPASS_PASSWORD]:-}"
        } > .env
        chmod 600 .env
        ok ".env written (0600)"
    fi
fi

# ---------------------------------------------------------------------------
# 5. storage
# ---------------------------------------------------------------------------
if [[ "$MODE" != check ]]; then
    step "5. Storage"
    if rclone listremotes 2>/dev/null | grep -q "^backblaze:"; then
        warn "rclone remote 'backblaze' already exists — left alone"
    elif [[ -n "${A[B2_KEY_ID]:-}" ]]; then
        rclone config create backblaze b2 \
            account "${A[B2_KEY_ID]}" key "${A[B2_APPLICATION_KEY]}" \
            >/dev/null 2>&1 && ok "rclone remote 'backblaze' created" \
            || bad "could not create the rclone remote"
    else
        warn "no Backblaze key given — storage not configured"
    fi
fi

# ---------------------------------------------------------------------------
# 6. configuration
# ---------------------------------------------------------------------------
CONF="config/backup.conf"
if [[ "$MODE" != check ]]; then
    step "6. Configuration"
    if [[ -f "$CONF" ]]; then
        warn "$CONF already exists — left alone"
    else
        cp config/backup.conf.example "$CONF" || die "no config/backup.conf.example"
        set_conf() {
            grep -q "^$1=" "$CONF" && sed -i "s|^$1=.*|$1=$2|" "$CONF" \
                                   || echo "$1=$2" >> "$CONF"
        }
        set_conf EMAIL_FROM       "${A[EMAIL_FROM]:-}"
        set_conf EMAIL_TO         "${A[EMAIL_TO]:-}"
        set_conf EMAIL_ENABLED    "$([[ -n "${A[EMAIL_TO]:-}" ]] && echo true || echo false)"
        set_conf TELEGRAM_CHAT_ID "${A[TELEGRAM_CHAT_ID]:-}"
        set_conf TELEGRAM_ENABLED "$([[ -n "${A[TELEGRAM_CHAT_ID]:-}" ]] && echo true || echo false)"
        # The names the rest of the system actually reads. B2_BUCKET is not
        # one of them — writing that left a fresh install with no bucket
        # configured and nothing to say so.
        set_conf BACKBLAZE_BUCKET  "${A[B2_BUCKET]:-}"
        set_conf BACKBLAZE_REMOTE  "$([[ -n "${A[B2_BUCKET]:-}" ]] && echo "backblaze:${A[B2_BUCKET]}")"
        set_conf BACKBLAZE_ENABLED "$([[ -n "${A[B2_BUCKET]:-}" ]] && echo true || echo false)"
        set_conf LOCAL_RETENTION_DAYS     "${A[LOCAL_RETENTION_DAYS]}"
        set_conf BACKBLAZE_RETENTION_DAYS "${A[BACKBLAZE_RETENTION_DAYS]}"
        set_conf USB_RETENTION_DAYS       "${A[USB_RETENTION_DAYS]}"
        set_conf INCREMENTAL "true"
        ok "written — ${A[LOCAL_RETENTION_DAYS]}d local, ${A[BACKBLAZE_RETENTION_DAYS]}d cloud"
    fi
    mkdir -p workspace logs state backupvault/{archives,hashes,manifests,reports,metadata}
    ok "folders ready"
fi

# ---------------------------------------------------------------------------
# 7. prove each piece works
# ---------------------------------------------------------------------------
step "7. Check-up"
echo "  ${DIM}Nothing below changes anything. It only asks each piece"
echo "  whether it answers when spoken to.${OFF}"
echo
python3 - <<'PY'
import os, sys, pathlib, subprocess, tempfile
sys.path.insert(0, os.getcwd())
GREEN, RED, YELLOW, OFF = "\033[32m", "\033[31m", "\033[33m", "\033[0m"
def ok(m):   print(f"  {GREEN}✓{OFF} {m}")
def bad(m):  print(f"  {RED}✗{OFF} {m}")
def warn(m): print(f"  {YELLOW}!{OFF} {m}")

# The Python packages, before anything that needs them. setup.sh installs
# these, but only pip's own failure was ever fatal — the browser step merely
# warned, so an install could finish looking complete with Notion unable to
# run at all. Checked by importing, because a package that pip says is there
# and Python cannot import is the case worth catching.
import importlib
required = {"textual": "textual", "rich": "rich", "msal": "msal",
            "requests": "requests", "cryptography": "cryptography",
            "notion-client": "notion_client", "playwright": "playwright"}
absent = []
for name, module in required.items():
    try:
        importlib.import_module(module)
    except Exception:
        absent.append(name)
if absent:
    bad(f"Python packages missing: {', '.join(absent)}")
    print("     pip3 install --user -r requirements.txt")
else:
    ok(f"all {len(required)} Python packages import")

# Notion's export drives a real browser. Without it that collector cannot
# run, and the failure would otherwise surface at 01:00 rather than now.
import shutil
browser = ""
try:
    from lib.secrets import get_config
    browser = get_config().get("NOTION_BROWSER_EXECUTABLE", "")
except Exception:
    pass
if not browser:
    warn("no NOTION_BROWSER_EXECUTABLE set — Notion's export will not run")
elif os.access(browser, os.X_OK):
    ok("Notion's browser is present")
else:
    bad(f"Notion's browser is not at {browser}")
    print("     python3 -m playwright install chromium")

try:
    from lib.secrets import load_env
    env = load_env()
    ok("credential database opens")
except Exception as e:
    bad(f"credential database: {str(e)[:90]}")
    raise SystemExit(1)

if not all(env.get(k) for k in ("TENANT_ID", "CLIENT_ID", "CLIENT_SECRET")):
    warn("Microsoft 365 not configured — skipped")
else:
    try:
        from collectors.m365.graph import get_token
        get_token(); ok("Microsoft 365 token acquired")
    except Exception as e:
        bad(f"Microsoft 365: {str(e)[:90]}")

if not env.get("CLOUDFLARE_API_TOKEN"):
    warn("Cloudflare not configured — skipped")
else:
    try:
        import requests
        # Not /user/tokens/verify: a token scoped to zone reads cannot read
        # its own definition and answers 401 there, which made a perfectly
        # good token look broken. Ask it to do the thing it is actually for.
        r = requests.get("https://api.cloudflare.com/client/v4/zones",
                         headers={"Authorization": f"Bearer {env['CLOUDFLARE_API_TOKEN']}"},
                         timeout=20)
        if r.ok:
            n = len((r.json().get("result") or []))
            ok(f"Cloudflare token valid ({n} zone{'s' if n != 1 else ''} visible)")
        else:
            bad(f"Cloudflare: HTTP {r.status_code}")
    except Exception as e:
        bad(f"Cloudflare: {str(e)[:90]}")

if not env.get("NOTION_TOKEN"):
    warn("Notion not configured — skipped")
else:
    try:
        import requests
        r = requests.get("https://api.notion.com/v1/users/me",
                         headers={"Authorization": f"Bearer {env['NOTION_TOKEN']}",
                                  "Notion-Version": "2022-06-28"}, timeout=20)
        ok("Notion token valid") if r.ok else bad(f"Notion: HTTP {r.status_code}")
    except Exception as e:
        bad(f"Notion: {str(e)[:90]}")

# The key has to round-trip, not merely exist. One that cannot open what it
# just sealed is worse than none at all, because everything looks fine until
# the day someone needs to restore.
key = pathlib.Path("config/keys/archive.key")
if not key.is_file():
    bad("no encryption key")
else:
    try:
        pub = subprocess.run(["age-keygen", "-y", str(key)],
                             capture_output=True, text=True).stdout.strip()
        with tempfile.TemporaryDirectory() as d:
            plain = pathlib.Path(d, "p"); plain.write_bytes(b"round trip")
            enc = pathlib.Path(d, "e")
            subprocess.run(["age", "-r", pub, "-o", str(enc), str(plain)], check=True)
            out = subprocess.run(["age", "-d", "-i", str(key), str(enc)],
                                 capture_output=True, check=True).stdout
        ok("encryption key seals and opens") if out == b"round trip" \
            else bad("encryption key did not round-trip")
    except Exception as e:
        bad(f"encryption: {str(e)[:90]}")
PY

REMOTE=$(grep -E "^BACKBLAZE_REMOTE=" "$CONF" 2>/dev/null | cut -d= -f2-)
if [[ -n "${REMOTE:-}" ]]; then
    timeout 60 rclone lsd "$REMOTE" >/dev/null 2>&1 \
        && ok "Backblaze '$REMOTE' reachable" \
        || bad "Backblaze '$REMOTE' could not be listed"
else
    warn "no BACKBLAZE_REMOTE set in $CONF"
fi

# ---------------------------------------------------------------------------
step "Where things stand"
if [[ "$MODE" == check ]]; then
    echo "  ${DIM}Nothing was changed.${OFF}"
else
    echo "  ${BOLD}Installation:${OFF} ${A[ORGANISATION]:-(unnamed)}"
fi
echo
echo "${BOLD}Next${OFF}"
echo "  ${CYAN}1.${OFF} Copy ${BOLD}config/keys/archive.key${OFF} somewhere offline."
echo "     ${DIM}Nothing in this system can recover it, by design.${OFF}"
echo "  ${CYAN}2.${OFF} ${BOLD}python3 -m orchestrator.run --tui${OFF}"
echo "     ${DIM}Set the schedule, then Back up now to prove a whole run.${OFF}"
echo "  ${CYAN}3.${OFF} ${BOLD}./office/install-to-drive.sh /media/…/DriveName${OFF}"
echo "     ${DIM}Only if this site keeps an external-drive copy.${OFF}"
echo
