"""Installing and editing the cron entries that run backups unattended.

The schedule lives in backup.conf, not in the crontab:

    CRON_ENABLED=true
    CRON_TIMEZONE=Asia/Kolkata
    M365_TIMES=01:00,13:00

Times are per service and get merged: every distinct moment becomes one
cron line naming the services due then, so services sharing a time share a
single run.

Ubuntu's cron is built without CRON_TZ support, so writing one in would be
silently ignored. The conversion to the machine's own zone happens here
instead, and each line carries a comment with the time it came from. Zones
that observe DST drift by an hour until the schedule is saved again;
observes_dst() reports that so the TUI can warn.

Only the block between the marker lines is touched.
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from .config import CFG, PROJECT_ROOT


BEGIN_MARKER = "# >>> HonestBackup >>>"
END_MARKER = "# <<< HonestBackup <<<"

WRAPPER = PROJECT_ROOT / "run_backup.sh"
CRON_LOG = PROJECT_ROOT / "logs" / "cron.log"

DEFAULT_TIMES = "01:00"


# ---------------------------------------------------------------------------
# clock times and time zones
# ---------------------------------------------------------------------------

def system_zone() -> ZoneInfo:
    """The zone cron itself fires in."""
    try:
        return ZoneInfo(Path("/etc/timezone").read_text().strip())
    except (OSError, ZoneInfoNotFoundError, ValueError):
        pass
    try:
        return ZoneInfo(Path("/etc/localtime").resolve().as_posix()
                        .split("zoneinfo/", 1)[1])
    except (OSError, IndexError, ZoneInfoNotFoundError, ValueError):
        return ZoneInfo("UTC")


def zone(name: str | None = None) -> ZoneInfo:
    """The zone the times are written in. Falls back to the machine's own."""
    name = (name if name is not None else CFG.get("CRON_TIMEZONE", "")).strip()
    if not name:
        return system_zone()
    try:
        return ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError):
        return system_zone()


def zone_name(name: str | None = None) -> str:
    return str(zone(name))


def valid_zone(name: str) -> bool:
    if not name.strip():
        return True
    try:
        ZoneInfo(name.strip())
        return True
    except (ZoneInfoNotFoundError, ValueError):
        return False


def observes_dst(name: str | None = None) -> bool:
    """True if the clocks in this zone change during the year."""
    tz = zone(name)
    year = date.today().year
    offsets = {
        datetime(year, month, 15, 12, tzinfo=tz).utcoffset()
        for month in (1, 4, 7, 10)
    }
    return len(offsets) > 1


def parse_times(text: str) -> list[tuple[int, int]]:
    """'01:00, 13:00' -> [(1, 0), (13, 0)]. Raises ValueError if malformed."""
    times: list[tuple[int, int]] = []
    for piece in str(text).replace(";", ",").split(","):
        piece = piece.strip()
        if not piece:
            continue
        if ":" in piece:
            hour_text, minute_text = piece.split(":", 1)
        else:
            hour_text, minute_text = piece, "0"
        try:
            hour, minute = int(hour_text), int(minute_text)
        except ValueError:
            raise ValueError(f"'{piece}' is not a time — write it like 13:00")
        if not 0 <= hour <= 23 or not 0 <= minute <= 59:
            raise ValueError(f"'{piece}' is not a real time of day")
        if (hour, minute) not in times:
            times.append((hour, minute))
    if not times:
        raise ValueError("give at least one time, for example 01:00, 13:00")
    return sorted(times)


def format_times(times: list[tuple[int, int]]) -> str:
    return ", ".join(f"{hour:02d}:{minute:02d}" for hour, minute in times)


# Each backed-up service, its on/off key, and the key holding its times.
SERVICES: list[tuple[str, str, str, str]] = [
    ("m365", "Microsoft 365", "ENABLE_M365", "M365_TIMES"),
    ("cloudflare", "Cloudflare", "ENABLE_CLOUDFLARE", "CLOUDFLARE_TIMES"),
    ("notion", "Notion", "ENABLE_NOTION", "NOTION_TIMES"),
]


def service_schedule(config: dict | None = None) -> dict[str, str]:
    """{service name: its times} for every service that is switched on."""
    source = config if config is not None else CFG
    schedule = {}
    for name, _, enable_key, times_key in SERVICES:
        if str(source.get(enable_key, "false")).strip().lower() != "true":
            continue
        times = str(source.get(times_key, DEFAULT_TIMES)).strip()
        if times:
            schedule[name] = times
    return schedule


def merge_schedule(schedule: dict[str, str],
                   timezone_name: str | None = None
                   ) -> list[tuple[str, list[str], str]]:
    """Collapse per-service times into one cron line per distinct moment.

    Returns [(cron expression, services due then, the time in words)],
    ordered by when they fire. Two services wanting 01:00 share a single
    run rather than starting two backups on top of each other.
    """
    source = zone(timezone_name)
    target = system_zone()
    today = date.today()

    moments: dict[tuple[int, int], set[str]] = {}
    for service, text in schedule.items():
        try:
            times = parse_times(text)
        except ValueError:
            continue
        for hour, minute in times:
            moments.setdefault((hour, minute), set()).add(service)

    lines = []
    for (hour, minute), services in sorted(moments.items()):
        local = datetime(today.year, today.month, today.day, hour, minute,
                         tzinfo=source).astimezone(target)
        expression = f"{local.minute} {local.hour} * * *"
        lines.append((expression, sorted(services), f"{hour:02d}:{minute:02d}"))
    return lines


def next_runs(text: str, timezone_name: str | None = None,
              count: int = 3) -> list[str]:
    """The next few firing times, written in the chosen zone."""
    try:
        times = parse_times(text)
    except ValueError:
        return []
    tz = zone(timezone_name)
    now = datetime.now(tz)
    upcoming: list[datetime] = []
    for day_offset in range(0, 8):
        day = now.date() + timedelta(days=day_offset)
        for hour, minute in times:
            moment = datetime(day.year, day.month, day.day, hour, minute,
                              tzinfo=tz)
            if moment > now:
                upcoming.append(moment)
        if len(upcoming) >= count:
            break
    return [m.strftime("%a %d %b · %H:%M") for m in sorted(upcoming)[:count]]


def enabled() -> bool:
    return str(CFG.get("CRON_ENABLED", "false")).strip().lower() == "true"


def describe(line: str) -> str:
    """A human sentence for one crontab line.

    Prefers the "# HH:MM Zone" comment install() writes on every line it
    creates — already converted to the zone the person chose, rather than
    the raw UTC-ish cron expression it compiles down to. Falls back to
    reading the raw fields, for a crontab line nobody here wrote.
    """
    if "#" in line:
        comment = line.rsplit("#", 1)[1].strip()
        if comment:
            return f"at {comment}"
    fields = line.split()
    if len(fields) >= 2 and fields[0].isdigit() and fields[1].isdigit():
        return f"at {int(fields[1]):02d}:{int(fields[0]):02d} on this machine's clock"
    return "on a schedule that could not be read"


@dataclass
class CronStatus:
    available: bool
    installed: bool
    schedule: str | None = None
    line: str | None = None
    error: str | None = None
    entries: int = 0

    @property
    def summary(self) -> str:
        if not self.available:
            return "cron is not available on this machine"
        if self.error:
            return self.error
        if not self.installed:
            return "automatic backups are off"
        if self.entries > 1:
            return f"automatic backups run at {self.entries} times a day"
        return f"automatic backups run {describe(self.line or '')}"


def _read_crontab() -> tuple[bool, list[str]]:
    """(crontab command works, current lines)."""
    try:
        proc = subprocess.run(
            ["crontab", "-l"], capture_output=True, text=True, timeout=15
        )
    except (OSError, subprocess.SubprocessError):
        return False, []
    if proc.returncode != 0:
        # "no crontab for user" is a normal empty state, not a failure.
        if "no crontab" in (proc.stderr or "").lower():
            return True, []
        return False, []
    return True, proc.stdout.splitlines()


def _write_crontab(lines: list[str]) -> str | None:
    """Install the given crontab; returns an error message or None."""
    body = "\n".join(lines).strip()
    if body:
        body += "\n"
    try:
        proc = subprocess.run(
            ["crontab", "-"], input=body, capture_output=True, text=True, timeout=15
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return str(exc)
    if proc.returncode != 0:
        return (proc.stderr or "could not write the crontab").strip()
    return None


def _without_our_block(lines: list[str]) -> list[str]:
    out: list[str] = []
    inside = False
    for line in lines:
        if line.strip() == BEGIN_MARKER:
            inside = True
            continue
        if line.strip() == END_MARKER:
            inside = False
            continue
        if not inside:
            out.append(line)
    return out


def status() -> CronStatus:
    available, lines = _read_crontab()
    if not available:
        return CronStatus(available=False, installed=False)
    inside = False
    found: list[str] = []
    for line in lines:
        stripped = line.strip()
        if stripped == BEGIN_MARKER:
            inside = True
            continue
        if stripped == END_MARKER:
            inside = False
            continue
        if inside and stripped and not stripped.startswith("#"):
            found.append(stripped)
    if not found:
        return CronStatus(available=True, installed=False)
    fields = found[0].split()
    return CronStatus(
        available=True,
        installed=True,
        schedule=" ".join(fields[:5]) if len(fields) >= 5 else None,
        line=found[0],
        entries=len(found),
    )


def install(schedule: dict[str, str] | None = None,
            timezone_name: str | None = None) -> str | None:
    """Write our cron block: one line per distinct time across all services.

    `schedule` is {service: times}; it defaults to whatever backup.conf says.
    Returns an error message, or None on success.
    """
    schedule = service_schedule() if schedule is None else schedule
    if not WRAPPER.exists():
        return f"{WRAPPER.name} is missing from the project folder"

    for service, text in schedule.items():
        try:
            parse_times(text)
        except ValueError as exc:
            return f"{service}: {exc}"

    merged = merge_schedule(schedule, timezone_name)
    if not merged:
        return "no services are switched on, so there is nothing to schedule"

    available, lines = _read_crontab()
    if not available:
        return "cron is not available on this machine"

    tz = zone_name(timezone_name)
    machine = system_zone()

    CRON_LOG.parent.mkdir(parents=True, exist_ok=True)
    block = [
        BEGIN_MARKER,
        "# Managed by HonestBackup — edit from the TUI's Scheduling screen.",
        f"# Times below are {machine}, which is the zone cron fires in.",
        f"# They were set as {tz} times; the comment on each line is the",
        f"# {tz} time it corresponds to.",
    ]
    for expression, services, wanted_at in merged:
        block.append(
            f"{expression} cd {PROJECT_ROOT} && "
            f"{WRAPPER} --only {','.join(services)} >> {CRON_LOG} 2>&1"
            f"  # {wanted_at} {tz}"
        )
    block.append(END_MARKER)

    return _write_crontab(_without_our_block(lines) + block)


def remove() -> str | None:
    """Take our cron entry out, leaving other entries untouched."""
    available, lines = _read_crontab()
    if not available:
        return "cron is not available on this machine"
    return _write_crontab(_without_our_block(lines))


def apply_from_config() -> str | None:
    """Make the machine's crontab match backup.conf."""
    return install() if enabled() else remove()
