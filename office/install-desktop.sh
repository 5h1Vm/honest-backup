#!/usr/bin/env bash
#
# Put a double-clickable launcher on the desktop and in the applications
# menu. Run this once, after pull.conf is set up.
#
# A .desktop file has to carry an absolute Exec path — it is launched from
# an arbitrary working directory — so the shipped template is rewritten
# here with wherever this folder actually ended up.

set -uo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TEMPLATE="$HERE/HonestBackup-Copy.desktop"
FILENAME="honestbackup-copy.desktop"
MENU_DIR="$HOME/.local/share/applications"

[[ -f "$TEMPLATE" ]] || { echo "  Stopped: $TEMPLATE is missing." >&2; exit 1; }

chmod +x "$HERE/copy-now.sh" "$HERE/pull.py" "$HERE/reseed.py" 2>/dev/null

write_launcher() {
    sed "s|^Exec=.*|Exec=\"$HERE/copy-now.sh\"|" "$TEMPLATE" > "$1"
    chmod +x "$1"
    # GNOME will not trust a launcher it did not write until it is marked so.
    command -v gio >/dev/null 2>&1 && gio set "$1" metadata::trusted true 2>/dev/null
}

echo
mkdir -p "$MENU_DIR"
write_launcher "$MENU_DIR/$FILENAME"
echo "  Added to the applications menu."

# The desktop folder is localised, so ask rather than assume "Desktop".
DESKTOP_DIR="$(xdg-user-dir DESKTOP 2>/dev/null || echo "$HOME/Desktop")"
if [[ -d "$DESKTOP_DIR" ]]; then
    write_launcher "$DESKTOP_DIR/$FILENAME"
    echo "  Added to the desktop: $DESKTOP_DIR"
else
    echo "  No desktop folder found, so the menu entry is all you get."
fi

command -v update-desktop-database >/dev/null 2>&1 \
    && update-desktop-database "$MENU_DIR" 2>/dev/null

echo
echo "  Double-click \"HonestBackup — Copy to Drive\" whenever the drive is"
echo "  plugged in. It opens a window, shows what it copied, and waits."
echo
