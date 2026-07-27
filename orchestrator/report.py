from pathlib import Path
from datetime import datetime
import json


class BackupReport:

    def __init__(self):

        self.report = {
            "backup_date": datetime.utcnow().date().isoformat(),
            "started_at": datetime.utcnow().isoformat() + "Z",
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
            "started_at": datetime.utcnow().isoformat() + "Z",
        }

    def collector_finish(self, name, **data):

        collector = self.report["collectors"][name]

        finished = datetime.utcnow()

        started = datetime.fromisoformat(
            collector["started_at"].replace("Z", "")
        )

        collector["finished_at"] = (
            finished.isoformat() + "Z"
        )

        collector["duration_seconds"] = round(
            (finished - started).total_seconds(),
            2
        )

        collector.update(data)

    def archive(self, **kwargs):

        self.report["archive"].update(kwargs)

    def verification(self, **kwargs):

        self.report["verification"].update(kwargs)

    def cleanup(self, **kwargs):

        self.report["cleanup"].update(kwargs)

    def warning(self, message):

        self.report["warnings"].append(
            message
        )

    def error(self, message):

        self.report["errors"].append(
            message
        )

    def finish(self):

        finished = datetime.utcnow()

        self.report["finished_at"] = (
            finished.isoformat() + "Z"
        )

        start = datetime.fromisoformat(
            self.report["started_at"].replace("Z", "")
        )

        self.report["duration_seconds"] = round(
            (finished - start).total_seconds(),
            2
        )

    def write(self, workspace):

        output = (
            Path(workspace)
            / "backup_report.json"
        )

        with open(output, "w") as f:

            json.dump(
                self.report,
                f,
                indent=4
            )

        return output