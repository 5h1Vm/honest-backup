#!/usr/bin/env bash
#
# Schedule the office copy on a Linux machine.
#
# Prefers a systemd timer over cron, because this runs on a laptop.
# Cron fires at a moment in time and if the machine is asleep, off, or shut
# for the evening at that moment, the run is simply skipped — cron never
# goes back for it. A laptop that gets closed at 18:55 would silently miss
# a 19:00 backup copy every single day, and nothing would say so.
#
# A systemd timer with Persistent=true catches up instead: it records when
# the job last ran, and if the machine was off when it was due, it runs as
# soon as the machine is back. That is the behaviour a laptop needs.
#
# Falls back to cron if systemd is not available.
#
# Usage:
#   ./install-schedule.sh              schedule daily at 19:00
#   ./install-schedule.sh 21:30        schedule daily at 21:30
#   ./install-schedule.sh --status     show what is scheduled
#   ./install-schedule.sh --remove     unschedule

set -uo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
UNIT_DIR="$HOME/.config/systemd/user"
NAME="honestbackup-pull"
TIME="${1:-19:00}"

have_systemd() {
    command -v systemctl >/dev/null 2>&1 && systemctl --user show-environment >/dev/null 2>&1
}

say()  { printf '  %s\n' "$*"; }
fail() { printf '\n  Stopped: %s\n\n' "$*" >&2; exit 1; }

# ---------------------------------------------------------------------------
# status / remove
# ---------------------------------------------------------------------------
if [[ "$TIME" == "--status" ]]; then
    if have_systemd && systemctl --user list-timers "$NAME.timer" --all 2>/dev/null | grep -q "$NAME"; then
        echo
        systemctl --user list-timers "$NAME.timer" --all
        echo
        say "Last run:"
        systemctl --user status "$NAME.service" --no-pager 2>/dev/null | tail -12
    elif crontab -l 2>/dev/null | grep -q "$NAME"; then
        echo; say "Scheduled with cron:"; crontab -l | grep "$NAME"
    else
        echo; say "Nothing is scheduled."
    fi
    echo
    exit 0
fi

if [[ "$TIME" == "--remove" ]]; then
    if have_systemd; then
        systemctl --user disable --now "$NAME.timer" 2>/dev/null
        rm -f "$UNIT_DIR/$NAME.timer" "$UNIT_DIR/$NAME.service"
        systemctl --user daemon-reload 2>/dev/null
    fi
    if crontab -l 2>/dev/null | grep -q "$NAME"; then
        crontab -l 2>/dev/null | grep -v "$NAME" | crontab -
    fi
    echo; say "Unscheduled. Nothing on the drive was touched."; echo
    exit 0
fi

# ---------------------------------------------------------------------------
# checks
# ---------------------------------------------------------------------------
[[ "$TIME" =~ ^([01][0-9]|2[0-3]):[0-5][0-9]$ ]] \
    || fail "'$TIME' is not a time. Write it like 19:00."

[[ -f "$HERE/pull.conf" ]] \
    || fail "pull.conf is missing. Copy pull.conf.example to pull.conf and edit it first."

command -v rclone >/dev/null 2>&1 \
    || fail "rclone is not installed. Try: sudo apt install rclone"

PYTHON="$(command -v python3)" || fail "python3 is not installed."

echo
say "Scheduling the office copy for $TIME every day."
echo

# ---------------------------------------------------------------------------
# systemd timer (preferred)
# ---------------------------------------------------------------------------
if have_systemd; then
    mkdir -p "$UNIT_DIR"

    cat > "$UNIT_DIR/$NAME.service" <<EOF
[Unit]
Description=HonestBackup — copy the archive down to the office drive
Documentation=file://$HERE/README.md

[Service]
Type=oneshot
WorkingDirectory=$HERE
ExecStart=$PYTHON $HERE/pull.py
# The drive being unplugged is an ordinary state, not a failure worth
# shouting about; pull.py exits cleanly and the next run picks it up.
SuccessExitStatus=0 1
EOF

    cat > "$UNIT_DIR/$NAME.timer" <<EOF
[Unit]
Description=HonestBackup — daily office copy at $TIME

[Timer]
OnCalendar=*-*-* $TIME:00
# The whole point on a laptop: if the machine was off or asleep when this
# was due, run it as soon as it is back rather than skipping the day.
Persistent=true
# Avoid every machine hitting Backblaze on the same second.
RandomizedDelaySec=300

[Install]
WantedBy=timers.target
EOF

    systemctl --user daemon-reload || fail "systemctl daemon-reload failed"
    systemctl --user enable --now "$NAME.timer" || fail "could not enable the timer"

    # Without lingering, user timers stop when the user logs out.
    if command -v loginctl >/dev/null 2>&1; then
        if ! loginctl show-user "$USER" 2>/dev/null | grep -q "Linger=yes"; then
            say "Allowing this to run when you are logged out:"
            sudo loginctl enable-linger "$USER" 2>/dev/null \
                && say "  enabled" \
                || say "  could not enable lingering — the copy will run only while you are logged in."
        fi
    fi

    echo
    say "Done — scheduled with systemd."
    systemctl --user list-timers "$NAME.timer" --no-pager 2>/dev/null | head -3
    echo
    say "Check on it any time with:  ./install-schedule.sh --status"
    say "Run it right now with:      python3 pull.py"
    echo
    exit 0
fi

# ---------------------------------------------------------------------------
# cron fallback
# ---------------------------------------------------------------------------
say "systemd is not available here, using cron instead."
say "Note: if this machine is off at $TIME, cron skips that day rather than"
say "catching up later. Leave it on at that hour, or plug the drive in and"
say "run 'python3 pull.py' by hand now and then."
echo

HOUR="${TIME%%:*}"; MINUTE="${TIME##*:}"
ENTRY="${MINUTE#0} ${HOUR#0} * * * cd $HERE && $PYTHON $HERE/pull.py >> $HERE/pull.log 2>&1  # $NAME"

{ crontab -l 2>/dev/null | grep -v "$NAME"; echo "$ENTRY"; } | crontab - \
    || fail "could not write the crontab"

say "Done — scheduled with cron:"
crontab -l | grep "$NAME"
echo
