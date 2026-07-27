from pathlib import Path
from datetime import datetime, timedelta
import time

IST_OFFSET = timedelta(hours=5, minutes=30)

def _now_ist():
    return datetime.utcnow() + IST_OFFSET


class Logger:

    INDENT = "    "

    def __init__(self, log_dir: Path, log_filename: str = "backup.log"):

        self.log_dir = Path(log_dir)

        self.log_dir.mkdir(
            parents=True,
            exist_ok=True
        )

        self.log_file = self.log_dir / log_filename

        self._indent = 0
        self._section_start = None

        self._write_raw("")
        self._write_raw("=" * 60)
        self._write_raw("Backup Started")
        self._write_raw(_now_ist().strftime("%Y-%m-%d %H:%M:%S"))
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

    def _write(self, level, message):

        timestamp = _now_ist().strftime(
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
        self._write_raw(_now_ist().strftime("%Y-%m-%d %H:%M:%S"))
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
