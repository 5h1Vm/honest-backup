import json

from pathlib import Path

from storage.artifact import BackupArtifact


class MetadataStore:
    def __init__(self, root: Path):
        self.file = root / "metadata" / "metadata.json"
        self.file.parent.mkdir(
            parents=True,
            exist_ok=True,
        )
        if not self.file.exists():
            self.file.write_text(
                json.dumps(
                    {
                        "version": 1,
                        "backups": [],
                        "latest_backup": None,
                        "total_backups": 0,
                    },
                    indent=4,
                )
            )

    def load(self):
        return json.loads(self.file.read_text())

    def save(self, data):
        self.file.write_text(
            json.dumps(
                data,
                indent=4,
            )
        )

    def append(
        self,
        artifact,
        session,
    ):
        if artifact is None:
            return

        data = self.load()
        entry = {
            "backup_id": artifact.backup_id,
            "created": session.started.isoformat(),
            "duration": session.duration,
            "verified": session.verified,
            "size": artifact.size,
            "archive": artifact.archive_name,
            "checksum": artifact.checksum_name,
            "replication": session.replicated,
            "collectors": session.collectors,
        }
        data["backups"].append(entry)
        data["latest_backup"] = artifact.backup_id
        data["total_backups"] = len(data["backups"])
        self.save(data)
