#!/usr/bin/env bash
#
# Put the whole office copy onto the external drive itself: the scripts, a
# copy of rclone, the remote definition, and a launcher you can double-click.
#
# After this, the drive works on any Linux machine with Python — plug it in,
# double-click, done. Nothing is left behind on the laptop.
#
#   ./install-to-drive.sh /media/you/DriveName
#
# About the credentials
#   The remote definition holds a Backblaze key. Putting it on the drive
#   means the key travels with the drive, so a stolen drive would give the
#   finder read access to the whole bucket — which is exactly what the
#   encrypted archives were meant to prevent.
#
#   So this script insists on one of two things:
#     - an rclone config password (the config is encrypted on the drive), or
#     - --no-credentials, which leaves the remote on the laptop instead.

set -uo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"

BOLD=$'\033[1m'; DIM=$'\033[2m'; RED=$'\033[31m'
GREEN=$'\033[32m'; YELLOW=$'\033[33m'; OFF=$'\033[0m'
ok(){ echo "  ${GREEN}✓${OFF} $*"; }
warn(){ echo "  ${YELLOW}!${OFF} $*"; }
die(){ echo; echo "  ${RED}Stopped:${OFF} $*"; echo; exit 1; }

DRIVE="${1:-}"
DRIVE="${DRIVE%/}"          # a trailing slash makes every path look like a//b
NO_CREDS=false
[[ "${2:-}" == "--no-credentials" || "${1:-}" == "--no-credentials" ]] && NO_CREDS=true
[[ "$DRIVE" == "--no-credentials" ]] && DRIVE="${2:-}"

[[ -n "$DRIVE" ]] || die "Give me the drive's mount point.
     Find it with:  lsblk -o NAME,LABEL,SIZE,MOUNTPOINT
     Then:          ./install-to-drive.sh /media/you/DriveName"

[[ -d "$DRIVE" ]] || die "$DRIVE does not exist. Is the drive mounted?"
[[ -w "$DRIVE" ]] || die "$DRIVE is not writable by $USER."

TOOL="$DRIVE/HonestBackup"
# The backups themselves live one level inside the HonestBackup folder,
# not loose at the drive's root — a drive is Finder-browsed as often as
# it is scripted, and archives/hashes/manifests/reports/metadata sitting
# next to volume icons and the scripts is not what "easy to navigate"
# looks like. One folder in, everything has a place.
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
mkdir -p "$TOOL" "$DATA" || die "could not create $TOOL"
for f in pull.py view.py reseed.py copy-now.sh sync.command get-tools.sh \
         HonestBackup.command pull.conf.example README.md; do
    cp "$HERE/$f" "$TOOL/" || die "could not copy $f"
done
chmod +x "$TOOL"/*.sh "$TOOL"/*.py "$TOOL"/*.command 2>/dev/null
ok "scripts copied to $TOOL"

# ---------------------------------------------------------------------------
# four launchers at the very top of the drive — two platforms, two speeds
#
#   Menu Mac / Menu Linux   open the menu: choose sync, look inside, check
#   Sync Mac / Sync Linux   do the sync and quit, for someone who already
#                           knows what they want
#
# Each is a stub, not a second copy of the real script: the real ones stay
# inside HonestBackup/, where they can find pull.py and tools/ beside
# them. Deleting one of these four and double-clicking the folder still
# works — they are convenience, not the only way in.
# ---------------------------------------------------------------------------
cat > "$DRIVE/Menu Mac.command" <<'STUB'
#!/bin/bash
cd "$(dirname "${BASH_SOURCE[0]}")/HonestBackup" && exec ./HonestBackup.command
STUB
chmod +x "$DRIVE/Menu Mac.command"

cat > "$DRIVE/Sync Mac.command" <<'STUB'
#!/bin/bash
cd "$(dirname "${BASH_SOURCE[0]}")/HonestBackup" && exec ./sync.command
STUB
chmod +x "$DRIVE/Sync Mac.command"

# Exec takes an absolute path, resolved here and now — %k is a URI, not a
# path, and dirname on it produces garbage, so it does not do what it
# looks like it does. This is the same tradeoff pull.conf's DESTINATION
# already makes: fixed to where the drive is mounted right now, and fixed
# again by re-running this script if that ever changes.
cat > "$DRIVE/Menu Linux.desktop" <<EOF
[Desktop Entry]
Type=Application
Version=1.0
Name=Menu Linux
Comment=Choose what to do with this drive
Icon=drive-harddisk
Terminal=true
Path=$TOOL
Exec="$TOOL/HonestBackup.command"
Categories=Utility;Archiving;
EOF
chmod +x "$DRIVE/Menu Linux.desktop"

cat > "$DRIVE/Sync Linux.desktop" <<EOF
[Desktop Entry]
Type=Application
Version=1.0
Name=Sync Linux
Comment=Bring the latest backups down from Backblaze
Icon=drive-harddisk
Terminal=true
Path=$TOOL
Exec="$TOOL/copy-now.sh"
Categories=Utility;Archiving;
EOF
chmod +x "$DRIVE/Sync Linux.desktop"

for launcher in "Menu Linux.desktop" "Sync Linux.desktop"; do
    command -v gio >/dev/null 2>&1 && \
        gio set "$DRIVE/$launcher" metadata::trusted true 2>/dev/null
done
ok "four launchers placed at the top of the drive"
echo "      ${DIM}Menu Mac.command    Sync Mac.command${OFF}"
echo "      ${DIM}Menu Linux.desktop  Sync Linux.desktop${OFF}"

# ---------------------------------------------------------------------------
# rclone, so the drive does not depend on the machine having it
# ---------------------------------------------------------------------------
SYSTEM_RCLONE="$(command -v rclone || true)"
if [[ -n "$SYSTEM_RCLONE" ]]; then
    mkdir -p "$TOOL/tools"
    cp "$SYSTEM_RCLONE" "$TOOL/tools/rclone" && chmod +x "$TOOL/tools/rclone"
    ok "rclone carried along ($("$TOOL/tools/rclone" version | head -1))"
else
    warn "no rclone to copy — the drive will need one on the machine it is used on"
fi

# That rclone only runs on machines like this one. For a drive that also
# has to open on the office Mac, get-tools.sh fetches a binary per
# platform; say so rather than letting it fail there.
echo "  ${DIM}For a drive that must also work on macOS:${OFF}"
echo "  ${DIM}  $TOOL/get-tools.sh --all${OFF}"

# ---------------------------------------------------------------------------
# the remote definition
# ---------------------------------------------------------------------------
LAPTOP_CONF="${RCLONE_CONFIG:-$REAL_HOME/.config/rclone/rclone.conf}"
if $NO_CREDS; then
    warn "leaving credentials on the laptop (--no-credentials)"
    warn "the drive will only work on machines that have the remote configured"
elif [[ -f "$LAPTOP_CONF" ]]; then
    if grep -q "^RCLONE_ENCRYPT_V0:" "$LAPTOP_CONF" 2>/dev/null; then
        cp "$LAPTOP_CONF" "$TOOL/rclone.conf"; chmod 600 "$TOOL/rclone.conf"
        ok "remote copied to the drive (already encrypted)"
    else
        echo
        echo "  ${YELLOW}The remote definition holds your Backblaze key.${OFF}"
        echo "  ${DIM}Copying it to the drive unencrypted means anyone who finds the"
        echo "  drive can read your whole cloud backup. Encrypt it instead:${OFF}"
        echo
        echo "    rclone config          →  s) Set configuration password"
        echo
        echo "  ${DIM}Then run this again. Or use --no-credentials to keep the"
        echo "  remote on the laptop only.${OFF}"
        die "refusing to copy an unencrypted key onto a portable drive"
    fi
else
    warn "no rclone config found at $LAPTOP_CONF"
fi

# ---------------------------------------------------------------------------
# configuration, pointed at the drive it lives on
# ---------------------------------------------------------------------------
if [[ -f "$TOOL/pull.conf" ]]; then
    ok "pull.conf already there, left alone — currently:"
    grep -E "^(REMOTE|DESTINATION)=" "$TOOL/pull.conf" | sed 's/^/      /'
    if ! grep -qE "^DESTINATION=$DATA\$" "$TOOL/pull.conf"; then
        warn "DESTINATION does not point at $DATA — edit $TOOL/pull.conf"
    fi
else
    sed -e "s|^DESTINATION=.*|DESTINATION=$DATA|" \
        "$HERE/pull.conf.example" > "$TOOL/pull.conf"
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
        sed -i "s|^REMOTE=.*|$REMOTE_LINE|" "$TOOL/pull.conf"
    # STATUS_REMOTE is a separate line from REMOTE, so setting REMOTE above
    # does not touch it — left at an example value it fails silently at
    # the very end of every pull, after everything real already worked.
    sed -i "s|^STATUS_REMOTE=.*|STATUS_REMOTE=|" "$TOOL/pull.conf"
    ok "pull.conf created, pointing at $DATA"
    grep -E "^(REMOTE|DESTINATION)=" "$TOOL/pull.conf" | sed 's/^/      /'
fi

# Whichever branch got here, a remote nobody has set is the single most
# likely reason the drive stays empty, so it is checked once at the end.
if grep -qE "^REMOTE=(backblaze:your-bucket)?$|^REMOTE=$" "$TOOL/pull.conf" \
   || grep -qE "^REMOTE=[^:]*:$" "$TOOL/pull.conf"; then
    warn "REMOTE is not set to a bucket yet — nothing will copy until it is"
    echo "        edit $TOOL/pull.conf and set, for example:"
    echo "          REMOTE=backblaze:honestbackup"
fi

if [[ -n "${SUDO_USER:-}" ]]; then
    chown -R "$REAL_USER" "$TOOL" \
        "$DRIVE/Menu Mac.command" "$DRIVE/Sync Mac.command" \
        "$DRIVE/Menu Linux.desktop" "$DRIVE/Sync Linux.desktop" 2>/dev/null \
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
echo "  Or from a terminal:  $TOOL/copy-now.sh"
echo
echo "  ${DIM}To read the backups on the drive rather than only carry them,"
echo "  the private key has to travel too:${OFF}"
echo "      $TOOL/get-tools.sh --with-key"
echo "  ${DIM}That makes a lost drive a readable copy of everything. Only do"
echo "  it for a drive that stays locked away.${OFF}"
echo
