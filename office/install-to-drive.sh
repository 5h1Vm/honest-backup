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
mkdir -p "$TOOL" || die "could not create $TOOL"
for f in pull.py reseed.py copy-now.sh pull.conf.example README.md; do
    cp "$HERE/$f" "$TOOL/" || die "could not copy $f"
done
chmod +x "$TOOL"/*.sh "$TOOL"/*.py 2>/dev/null
ok "scripts copied to $TOOL"

# ---------------------------------------------------------------------------
# rclone, so the drive does not depend on the machine having it
# ---------------------------------------------------------------------------
SYSTEM_RCLONE="$(command -v rclone || true)"
if [[ -n "$SYSTEM_RCLONE" ]]; then
    cp "$SYSTEM_RCLONE" "$TOOL/rclone" && chmod +x "$TOOL/rclone"
    ok "rclone carried along ($("$TOOL/rclone" version | head -1))"
else
    warn "no rclone to copy — the drive will need one on the machine it is used on"
fi

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
    if grep -qE "^REMOTE=backblaze:your-bucket" "$TOOL/pull.conf"; then
        warn "REMOTE is still the example value — edit $TOOL/pull.conf"
    fi
    if ! grep -qE "^DESTINATION=$DRIVE\$" "$TOOL/pull.conf"; then
        warn "DESTINATION does not point at this drive — edit $TOOL/pull.conf"
    fi
else
    sed -e "s|^DESTINATION=.*|DESTINATION=$DRIVE|" \
        "$HERE/pull.conf.example" > "$TOOL/pull.conf"
    if [[ -f "$HERE/pull.conf" ]]; then
        REMOTE_LINE=$(grep -E "^\s*REMOTE\s*=" "$HERE/pull.conf" | head -1)
        [[ -n "$REMOTE_LINE" ]] && sed -i "s|^REMOTE=.*|$REMOTE_LINE|" "$TOOL/pull.conf"
    fi
    ok "pull.conf created, pointing at $DRIVE"
    grep -E "^(REMOTE|DESTINATION)=" "$TOOL/pull.conf" | sed 's/^/      /'
fi

# ---------------------------------------------------------------------------
# a launcher on the drive itself
# ---------------------------------------------------------------------------
cat > "$DRIVE/Copy backups to this drive.desktop" <<EOF
[Desktop Entry]
Type=Application
Version=1.0
Name=Copy backups to this drive
Comment=Bring the latest backups down from Backblaze
Icon=drive-harddisk
Terminal=true
Exec=bash -c 'cd "\$(dirname "%k")/HonestBackup" && ./copy-now.sh'
Categories=Utility;Archiving;
EOF
chmod +x "$DRIVE/Copy backups to this drive.desktop"
command -v gio >/dev/null 2>&1 && \
    gio set "$DRIVE/Copy backups to this drive.desktop" metadata::trusted true 2>/dev/null
ok "launcher placed at the top of the drive"

if [[ -n "${SUDO_USER:-}" ]]; then
    chown -R "$REAL_USER" "$TOOL" "$DRIVE/Copy backups to this drive.desktop" 2>/dev/null \
        && ok "ownership handed to $REAL_USER" \
        || warn "could not change ownership — you may need sudo to run it"
fi

echo
echo "${BOLD}Done${OFF}"
echo "  Plug this drive into any Linux machine with Python and double-click"
echo "  ${DIM}Copy backups to this drive${OFF} at the top of it."
echo
echo "  Or from a terminal:  $TOOL/copy-now.sh"
echo
