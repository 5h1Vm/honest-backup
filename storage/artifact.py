from dataclasses import dataclass
from pathlib import Path
from datetime import datetime


@dataclass(slots=True)
class BackupArtifact:
    """
    Represents one immutable backup.
    """

    backup_id: str

    created: datetime

    archive: Path

    sha256: Path

    manifest: Path | None = None

    report: Path | None = None

    size: int = 0

    @property
    def archive_name(self):

        return self.archive.name

    @property
    def checksum_name(self):

        return self.sha256.name

    @property
    def manifest_name(self):

        if self.manifest is None:
            return None

        return self.manifest.name
