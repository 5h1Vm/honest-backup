from dataclasses import dataclass, field
from datetime import datetime

from storage.artifact import BackupArtifact


@dataclass(slots=True)
class BackupSession:
    backup_id: str

    started: datetime

    finished: datetime | None = None

    artifact: BackupArtifact | None = None

    collectors: list[str] = field(default_factory=list)

    warnings: list[str] = field(default_factory=list)

    errors: list[str] = field(default_factory=list)

    verified: bool = False

    skipped: bool = False

    replicated: dict = field(default_factory=dict)

    def finish(self):
        self.finished = datetime.now()

    @property
    def duration(self):
        if self.finished is None:
            return None

        return (self.finished - self.started).total_seconds()
