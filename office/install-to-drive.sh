#!/usr/bin/env bash
#
# Put the whole office copy onto the external drive itself: the scripts, a
# copy of rclone, the remote definition, and launchers you can double-click.
#
# After this, the drive works on any Linux or Mac machine with Python —
# plug it in, double-click, done. Nothing is left behind on the laptop.
#
#   ./install-to-drive.sh /media/you/DriveName
#
# About the credentials
#   The remote definition holds a Backblaze key. Putting it on the drive
#   means the key travels with the drive, so a stolen drive would give the
#   finder read access to the whole bucket — which is exactly what the
#   encrypted archives were meant to prevent.
#
#   So this script insists on one of three things:
#     - an rclone config password (the config is encrypted on the drive),
#     - --no-credentials, which leaves the remote on the laptop instead, or
#     - --plaintext-credentials, an explicit "I accept the risk" for a
#       drive that needs to work on any machine with no password prompt
#       at all — the key travels in the open.

set -uo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"

BOLD=$'\033[1m'; DIM=$'\033[2m'; RED=$'\033[31m'
GREEN=$'\033[32m'; YELLOW=$'\033[33m'; OFF=$'\033[0m'
ok(){ echo "  ${GREEN}✓${OFF} $*"; }
warn(){ echo "  ${YELLOW}!${OFF} $*"; }
die(){ echo; echo "  ${RED}Stopped:${OFF} $*"; echo; exit 1; }

NO_CREDS=false
PLAINTEXT_OK=false
ARGS=()
for arg in "$@"; do
    case "$arg" in
        --no-credentials)      NO_CREDS=true ;;
        --plaintext-credentials) PLAINTEXT_OK=true ;;
        *) ARGS+=("$arg") ;;
    esac
done
DRIVE="${ARGS[0]:-}"
DRIVE="${DRIVE%/}"          # a trailing slash makes every path look like a//b

[[ -n "$DRIVE" ]] || die "Give me the drive's mount point.
     Find it with:  lsblk -o NAME,LABEL,SIZE,MOUNTPOINT
     Then:          ./install-to-drive.sh /media/you/DriveName"

[[ -d "$DRIVE" ]] || die "$DRIVE does not exist. Is the drive mounted?"
[[ -w "$DRIVE" ]] || die "$DRIVE is not writable by $USER."

# Three folders, three jobs, nothing loose:
#
#   HonestBackup/
#     README.md          what this is, in one page
#     Backups/            the data — this is what someone is actually
#                          looking for when they open this drive
#     Scripts/             everything that makes the data arrive here.
#                          Nobody needs to open this folder to use the
#                          drive; it exists so the launchers have
#                          something to run.
TOOL="$DRIVE/HonestBackup"
ENGINE="$TOOL/Scripts"
DATA="$TOOL/Backups"

# Run under sudo, $HOME is /root and the rclone config is not there. Work out
# who actually owns the session so the config is found and the files end up
# owned by a person rather than root.
REAL_USER="${SUDO_USER:-$USER}"
REAL_HOME="$(getent passwd "$REAL_USER" | cut -d: -f6)"
REAL_HOME="${REAL_HOME:-$HOME}"

echo
echo "${BOLD}Installing onto $DRIVE${OFF}"
if [[ -n "${SUDO_USER:-}" ]]; then
    echo "  ${DIM}running as root on behalf of $REAL_USER${OFF}"
fi
echo

# ---------------------------------------------------------------------------
# the scripts
# ---------------------------------------------------------------------------
mkdir -p "$ENGINE" "$DATA" || die "could not create $ENGINE"
for f in pull.py view.py reseed.py copy-now.sh sync.command get-tools.sh \
         HonestBackup.command pull.conf.example; do
    cp "$HERE/$f" "$ENGINE/" || die "could not copy $f"
done
cp "$HERE/README.md" "$TOOL/README.md" || die "could not copy README.md"
chmod +x "$ENGINE"/*.sh "$ENGINE"/*.py "$ENGINE"/*.command 2>/dev/null
ok "scripts tucked into $ENGINE, out of the way of the backups themselves"

# ---------------------------------------------------------------------------
# four launchers at the very top of the drive — two platforms, two speeds
#
#   Menu Mac / Menu Linux   open the menu: choose sync, look inside, check
#   Sync Mac / Sync Linux   do the sync and quit, for someone who already
#                           knows what they want
#
# Each is a stub, not a second copy of the real script: the real ones stay
# inside Scripts/, where they can find pull.py and tools/ beside them.
# Deleting one of these four and going into HonestBackup/Scripts/ directly
# still works — they are convenience, not the only way in.
# ---------------------------------------------------------------------------
cat > "$DRIVE/Menu Mac.command" <<'STUB'
#!/bin/bash
cd "$(dirname "${BASH_SOURCE[0]}")/HonestBackup/Scripts" && exec ./HonestBackup.command
STUB
chmod +x "$DRIVE/Menu Mac.command"

cat > "$DRIVE/Sync Mac.command" <<'STUB'
#!/bin/bash
cd "$(dirname "${BASH_SOURCE[0]}")/HonestBackup/Scripts" && exec ./sync.command
STUB
chmod +x "$DRIVE/Sync Mac.command"

# .desktop files were tried here first and dropped: how a file manager
# handles one is not standard the way the freedesktop spec implies. On
# one real machine tested against, Thunar tried to add "Sync Linux.desktop"
# as an XFCE PANEL PLUGIN on double-click instead of running it, and a
# second launch path handed the file's raw text straight to bash, which
# choked reading "[Desktop Entry]" as a command. GNOME, XFCE and whatever
# hybrid a given Linux machine runs do not agree on what a .desktop file
# double-click means. A shebang script is not ambiguous anywhere — it is
# what a shell runs, full stop — so that is what these are instead, the
# same convention already used for .command on macOS.
cat > "$DRIVE/Menu Linux.sh" <<'STUB'
#!/bin/bash
cd "$(dirname "${BASH_SOURCE[0]}")/HonestBackup/Scripts" && exec ./HonestBackup.command
STUB
chmod +x "$DRIVE/Menu Linux.sh"

cat > "$DRIVE/Sync Linux.sh" <<'STUB'
#!/bin/bash
cd "$(dirname "${BASH_SOURCE[0]}")/HonestBackup/Scripts" && exec ./copy-now.sh
STUB
chmod +x "$DRIVE/Sync Linux.sh"

# Leftovers from an older install of this same drive would otherwise sit
# there confusing things forever.
rm -f "$DRIVE/Menu Linux.desktop" "$DRIVE/Sync Linux.desktop"
rm -f "$TOOL/pull.py" "$TOOL/view.py" "$TOOL/reseed.py" "$TOOL/copy-now.sh" \
      "$TOOL/sync.command" "$TOOL/get-tools.sh" "$TOOL/HonestBackup.command" \
      "$TOOL/pull.conf.example"
[[ -d "$TOOL/tools" && ! -d "$ENGINE/tools" ]] && mv "$TOOL/tools" "$ENGINE/tools"
# A key from an older install that did copy one onto the drive — the
# policy now is that the key never lives here, so this removes it rather
# than carrying it forward.
if [[ -d "$TOOL/keys" ]]; then
    rm -rf "$TOOL/keys"
    warn "removed a private key found on the drive from an older install"
fi
if [[ -d "$ENGINE/keys" ]]; then
    rm -rf "$ENGINE/keys"
    warn "removed a private key found on the drive from an older install"
fi
[[ -f "$TOOL/pull.conf" && ! -f "$ENGINE/pull.conf" ]] && mv "$TOOL/pull.conf" "$ENGINE/pull.conf"
[[ -f "$TOOL/rclone.conf" && ! -f "$ENGINE/rclone.conf" ]] && mv "$TOOL/rclone.conf" "$ENGINE/rclone.conf"

for launcher in "Menu Mac.command" "Sync Mac.command" "Menu Linux.sh" "Sync Linux.sh"; do
    command -v gio >/dev/null 2>&1 && \
        gio set "$DRIVE/$launcher" metadata::trusted true 2>/dev/null
done
ok "four launchers placed at the top of the drive"
echo "      ${DIM}Menu Mac.command   Sync Mac.command${OFF}"
echo "      ${DIM}Menu Linux.sh      Sync Linux.sh${OFF}"
echo "      ${DIM}A Linux file manager may ask once whether to run or view"
echo "      a .sh file — choose Run. That choice is usually remembered.${OFF}"

# ---------------------------------------------------------------------------
# rclone, so the drive does not depend on the machine having it
# ---------------------------------------------------------------------------
SYSTEM_RCLONE="$(command -v rclone || true)"
if [[ -n "$SYSTEM_RCLONE" ]]; then
    mkdir -p "$ENGINE/tools"
    cp "$SYSTEM_RCLONE" "$ENGINE/tools/rclone" && chmod +x "$ENGINE/tools/rclone"
    ok "rclone carried along ($("$ENGINE/tools/rclone" version | head -1))"
else
    warn "no rclone to copy — the drive will need one on the machine it is used on"
fi

# That rclone only runs on machines like this one, and age/zstd are not
# carried at all yet. For a drive that must also open on the office Mac,
# get-tools.sh --all fetches a full set per platform; say so rather than
# letting it fail there with only rclone present.
echo "  ${DIM}For a drive that must also work on macOS, or to read backups"
echo "  from this drive rather than only carry them:${OFF}"
echo "      $ENGINE/get-tools.sh --all"

# ---------------------------------------------------------------------------
# the remote definition
# ---------------------------------------------------------------------------
LAPTOP_CONF="${RCLONE_CONFIG:-$REAL_HOME/.config/rclone/rclone.conf}"
if $NO_CREDS; then
    warn "leaving credentials on the laptop (--no-credentials)"
    warn "the drive will only work on machines that have the remote configured"
elif [[ -f "$LAPTOP_CONF" ]]; then
    if grep -q "^RCLONE_ENCRYPT_V0:" "$LAPTOP_CONF" 2>/dev/null; then
        cp "$LAPTOP_CONF" "$ENGINE/rclone.conf"; chmod 600 "$ENGINE/rclone.conf"
        ok "remote copied to the drive (already encrypted)"
    elif $PLAINTEXT_OK; then
        cp "$LAPTOP_CONF" "$ENGINE/rclone.conf"; chmod 600 "$ENGINE/rclone.conf"
        warn "remote copied to the drive UNENCRYPTED (--plaintext-credentials)"
        warn "anyone who finds this drive can read the whole Backblaze bucket"
    else
        echo
        echo "  ${YELLOW}The remote definition holds your Backblaze key.${OFF}"
        echo "  ${DIM}Copying it to the drive unencrypted means anyone who finds the"
        echo "  drive can read your whole cloud backup. Encrypt it instead:${OFF}"
        echo
        echo "    rclone config          →  s) Set configuration password"
        echo
        echo "  ${DIM}Then run this again. Or use --no-credentials to keep the"
        echo "  remote on the laptop only, or --plaintext-credentials to copy"
        echo "  it as-is and accept that risk.${OFF}"
        die "refusing to copy an unencrypted key onto a portable drive"
    fi
else
    warn "no rclone config found at $LAPTOP_CONF"
fi

# ---------------------------------------------------------------------------
# configuration, pointed at the drive it lives on
# ---------------------------------------------------------------------------
if [[ -f "$ENGINE/pull.conf" ]]; then
    ok "pull.conf already there, left alone — currently:"
    grep -E "^(REMOTE|DESTINATION)=" "$ENGINE/pull.conf" | sed 's/^/      /'
    if ! grep -qE "^DESTINATION=$DATA\$" "$ENGINE/pull.conf"; then
        warn "DESTINATION does not point at $DATA — edit $ENGINE/pull.conf"
    fi
else
    sed -e "s|^DESTINATION=.*|DESTINATION=$DATA|" \
        "$HERE/pull.conf.example" > "$ENGINE/pull.conf"
    # Take the remote from a pull.conf sitting next to this script, and
    # failing that from the only remote rclone knows about — a drive that
    # ships pointing at "your-bucket" copies nothing and explains nothing.
    REMOTE_LINE=""
    [[ -f "$HERE/pull.conf" ]] && \
        REMOTE_LINE=$(grep -E "^\s*REMOTE\s*=" "$HERE/pull.conf" | head -1)
    if [[ -z "$REMOTE_LINE" ]]; then
        ONLY_REMOTE=$(rclone listremotes 2>/dev/null | head -2)
        if [[ "$(wc -l <<<"$ONLY_REMOTE")" -eq 1 && -n "$ONLY_REMOTE" ]]; then
            REMOTE_LINE="REMOTE=${ONLY_REMOTE}"
        fi
    fi
    [[ -n "$REMOTE_LINE" ]] && \
        sed -i "s|^REMOTE=.*|$REMOTE_LINE|" "$ENGINE/pull.conf"
    # STATUS_REMOTE is a separate line from REMOTE, so setting REMOTE above
    # does not touch it — left at an example value it fails silently at
    # the very end of every pull, after everything real already worked.
    sed -i "s|^STATUS_REMOTE=.*|STATUS_REMOTE=|" "$ENGINE/pull.conf"
    ok "pull.conf created, pointing at $DATA"
    grep -E "^(REMOTE|DESTINATION)=" "$ENGINE/pull.conf" | sed 's/^/      /'
fi

# Whichever branch got here, a remote nobody has set is the single most
# likely reason the drive stays empty, so it is checked once at the end.
if grep -qE "^REMOTE=(backblaze:your-bucket)?$|^REMOTE=$" "$ENGINE/pull.conf" \
   || grep -qE "^REMOTE=[^:]*:$" "$ENGINE/pull.conf"; then
    warn "REMOTE is not set to a bucket yet — nothing will copy until it is"
    echo "        edit $ENGINE/pull.conf and set, for example:"
    echo "          REMOTE=backblaze:honestbackup"
fi

if [[ -n "${SUDO_USER:-}" ]]; then
    chown -R "$REAL_USER" "$TOOL" \
        "$DRIVE/Menu Mac.command" "$DRIVE/Sync Mac.command" \
        "$DRIVE/Menu Linux.sh" "$DRIVE/Sync Linux.sh" 2>/dev/null \
        && ok "ownership handed to $REAL_USER" \
        || warn "could not change ownership — you may need sudo to run it"
fi

echo
echo "${BOLD}Done${OFF}  — four launchers at the top of the drive"
echo
echo "  ${BOLD}Sync Mac${OFF} / ${BOLD}Sync Linux${OFF}   the fast one — bring backups down and quit"
echo "  ${BOLD}Menu Mac${OFF} / ${BOLD}Menu Linux${OFF}   choose: sync, look inside, check, re-verify"
echo
echo "  ${DIM}First time on a new machine, use Menu — it can fetch what is"
echo "  needed onto the drive, and installs nothing on the machine itself.${OFF}"
echo
echo "  Or from a terminal:  $ENGINE/copy-now.sh"
echo
echo "  ${DIM}The private key never travels on the drive. To read a backup,"
echo "  Browse asks for it — pasted in, never saved.${OFF}"
echo
