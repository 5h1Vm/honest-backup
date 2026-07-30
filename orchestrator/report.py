from pathlib import Path
from datetime import datetime
import json

from lib.logger import display_zone


def _now() -> datetime:
    """Now, in CRON_TIMEZONE — matches the log lines and the backup ID
    this report describes, rather than whatever zone the machine's clock
    happens to be set to. isoformat() on this carries its own real offset
    (+05:30), so nothing downstream mistakes it for UTC the way the old
    manually-appended "Z" did."""
    return datetime.now(display_zone())


class BackupReport:
    def __init__(self):
        now = _now()
        self.report = {
            "backup_date": now.date().isoformat(),
            "started_at": now.isoformat(),
            "finished_at": None,
            "duration_seconds": None,
            "collectors": {},
            "archive": {},
            "verification": {},
            "cleanup": {},
            "warnings": [],
            "errors": [],
        }

    def collector_start(self, name):
        self.report["collectors"][name] = {
            "status": "running",
            "started_at": _now().isoformat(),
        }

    def collector_finish(self, name, **data):
        collector = self.report["collectors"][name]
        finished = _now()
        started = datetime.fromisoformat(collector["started_at"])
        collector["finished_at"] = finished.isoformat()
        collector["duration_seconds"] = round((finished - started).total_seconds(), 2)
        collector.update(data)

    def archive(self, **kwargs):
        self.report["archive"].update(kwargs)

    def verification(self, **kwargs):
        self.report["verification"].update(kwargs)

    def cleanup(self, **kwargs):
        self.report["cleanup"].update(kwargs)

    def warning(self, message):
        self.report["warnings"].append(message)

    def error(self, message):
        self.report["errors"].append(message)

    def finish(self):
        finished = _now()
        self.report["finished_at"] = finished.isoformat()
        start = datetime.fromisoformat(self.report["started_at"])
        self.report["duration_seconds"] = round((finished - start).total_seconds(), 2)

    def write(self, workspace):
        output = Path(workspace) / "backup_report.json"
        with open(output, "w") as f:
            json.dump(self.report, f, indent=4)

        return output
