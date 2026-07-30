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
        from lib.logger import display_zone

        # Matches started's zone, whatever that is — duration is a
        # subtraction, and mixing an aware and a naive datetime there
        # raises rather than quietly giving the wrong number.
        self.finished = datetime.now(self.started.tzinfo or display_zone())

    @property
    def duration(self):
        if self.finished is None:
            return None

        return (self.finished - self.started).total_seconds()
