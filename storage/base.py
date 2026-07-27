from abc import ABC, abstractmethod
from pathlib import Path

from .artifact import BackupArtifact


class StorageProvider(ABC):

    @property
    @abstractmethod
    def name(self) -> str:
        ...

    @abstractmethod
    def enabled(self) -> bool:
        ...

    @abstractmethod
    def healthcheck(self) -> bool:
        ...

    @abstractmethod
    def upload(
        self,
        artifact: BackupArtifact,
    ) -> bool:
        ...

    @abstractmethod
    def download(
        self,
        backup_name: str,
        destination: Path,
    ) -> bool:
        ...

    @abstractmethod
    def delete(
        self,
        backup_name: str,
    ) -> bool:
        ...

    @abstractmethod
    def exists(
        self,
        backup_name: str,
    ) -> bool:
        ...

    @abstractmethod
    def list_backups(self):
        ...