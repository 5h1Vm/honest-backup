from pathlib import Path
from datetime import datetime
import time
from zoneinfo import ZoneInfo


def display_zone():
    """The zone every human-facing timestamp is written in.

    Public alias of _display_zone — used outside this module wherever a
    backup ID, filename, or report date needs to agree with the log lines
    it sits next to, rather than falling back to whatever zone the
    machine's clock happens to be set to.
    """
    return _display_zone()


def _display_zone():
    """The zone log timestamps are written in.

    Follows CRON_TIMEZONE so the times in the log line up with the times on
    the Scheduling screen. This used to be a hardcoded +5:30 offset, which
    silently disagreed with the machine clock and with any other time zone.
    """
    try:
        from orchestrator.config import CFG
        name = str(CFG.get("CRON_TIMEZONE", "")).strip()
        if name:
            return ZoneInfo(name)
    except Exception:
        pass
    try:
        return ZoneInfo(Path("/etc/timezone").read_text().strip())
    except Exception:
        return None


def _now():
    zone = _display_zone()
    return datetime.now(zone) if zone else datetime.now()


class Logger:
    """Writes the run log.

    LOG_LEVEL in backup.conf decides how much gets through:

        QUIET    warnings and errors only
        NORMAL   the default: progress, results, warnings, errors
        VERBOSE  everything, including per-item debug detail

    It used to be a setting the interface offered and nothing read.
    """

    INDENT = "    "

    LEVELS = {"QUIET": 0, "NORMAL": 1, "VERBOSE": 2}

    # The lowest verbosity at which each kind of line is printed.
    THRESHOLD = {
        "DEBUG": 2,
        "INFO": 1,
        "SUCCESS": 1,
        "WARNING": 0,
        "ERROR": 0,
    }

    def __init__(self, log_dir: Path, log_filename: str = "backup.log",
                 level: str | None = None):

        self.log_dir = Path(log_dir)

        self.log_dir.mkdir(
            parents=True,
            exist_ok=True
        )

        self.log_file = self.log_dir / log_filename

        self._indent = 0
        self._section_start = None
        self.level = self._resolve_level(level)

        self._write_raw("")
        self._write_raw("=" * 60)
        self._write_raw("Backup Started")
        self._write_raw(_now().strftime("%Y-%m-%d %H:%M:%S"))
        self._write_raw("=" * 60)
        self._write_raw("")

    def _write_raw(self, text):

        print(text)

        with open(
            self.log_file,
            "a",
            encoding="utf-8"
        ) as f:

            f.write(text + "\n")

    @classmethod
    def _resolve_level(cls, level=None):
        if level is None:
            try:
                from orchestrator.config import CFG
                level = CFG.get("LOG_LEVEL", "NORMAL")
            except Exception:
                level = "NORMAL"
        name = str(level).strip().upper()
        # Tolerate the level names people reach for out of habit.
        name = {"INFO": "NORMAL", "DEFAULT": "NORMAL", "WARN": "QUIET",
                "WARNING": "QUIET", "ERROR": "QUIET", "DEBUG": "VERBOSE",
                "TRACE": "VERBOSE", "ALL": "VERBOSE"}.get(name, name)
        return cls.LEVELS.get(name, 1)

    def _write(self, level, message):

        if self.THRESHOLD.get(level, 1) > self.level:
            return

        timestamp = _now().strftime(
            "%Y-%m-%d %H:%M:%S"
        )

        indent = self.INDENT * self._indent

        line = (
            f"{indent}"
            f"[{timestamp}] "
            f"[{level}] "
            f"{message}"
        )

        self._write_raw(line)

    def banner(self, title):

        self._write_raw("")
        self._write_raw("=" * 60)
        self._write_raw(title)
        self._write_raw("=" * 60)

    def push(self):

        self._indent += 1

    def pop(self):

        if self._indent:

            self._indent -= 1

    def section(self, title):

        self.banner(title)

        self._section_start = time.time()

    def end_section(self):

        if self._section_start is None:
            return

        elapsed = (
            time.time() -
            self._section_start
        )

        self.success(
            f"Completed in {elapsed:.2f} seconds"
        )

        self._section_start = None

    def finish(self):

        self._write_raw("")
        self._write_raw("=" * 60)
        self._write_raw("Backup Finished")
        self._write_raw(_now().strftime("%Y-%m-%d %H:%M:%S"))
        self._write_raw("=" * 60)

    def info(self, message):

        self._write(
            "INFO",
            message
        )

    def success(self, message):

        self._write(
            "SUCCESS",
            message
        )

    def warning(self, message):

        self._write(
            "WARNING",
            message
        )

    def error(self, message):

        self._write(
            "ERROR",
            message
        )

    def debug(self, message):

        self._write(
            "DEBUG",
            message
        )
