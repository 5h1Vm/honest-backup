import shutil

from pathlib import Path

from .artifact import BackupArtifact
from .base import StorageProvider
from .config import (
    REPOSITORY_PATH,
    REPOSITORY_ENABLED,
)


class Repository(StorageProvider):
    name = "REPOSITORY"

    def __init__(self):
        self.root = Path(REPOSITORY_PATH)

    def enabled(self) -> bool:
        return REPOSITORY_ENABLED

    def healthcheck(self) -> bool:
        self.root.mkdir(
            parents=True,
            exist_ok=True,
        )
        return True

    def upload(
        self,
        artifact: BackupArtifact,
    ) -> bool:
        archives = self.root / "archives"
        hashes = self.root / "hashes"
        manifests = self.root / "manifests"
        archives.mkdir(parents=True, exist_ok=True)
        hashes.mkdir(parents=True, exist_ok=True)
        manifests.mkdir(parents=True, exist_ok=True)
        self._copy(artifact.archive, archives / artifact.archive.name)
        self._copy(artifact.sha256, hashes / artifact.sha256.name)
        if artifact.manifest:
            self._copy(artifact.manifest, manifests / artifact.manifest.name)

        return True

    def download(
        self,
        backup_name: str,
        destination: Path,
    ) -> bool:
        archive = self.root / "archives" / f"{backup_name}.tar.zst.age"
        if not archive.exists():
            return False

        destination.mkdir(parents=True, exist_ok=True)
        shutil.copy2(archive, destination / archive.name)
        return True

    def delete(
        self,
        backup_name: str,
    ) -> bool:
        removed = False
        for folder, ext in [
            ("archives", ".tar.zst.age"),
            ("hashes", ".sha256"),
            ("manifests", ".manifest.json"),
        ]:
            file = self.root / folder / f"{backup_name}{ext}"
            if file.exists():
                file.unlink()
                removed = True

        return removed

    def exists(
        self,
        backup_name: str,
    ) -> bool:
        return (self.root / "archives" / f"{backup_name}.tar.zst.age").exists()

    def list_backups(self):
        archives = self.root / "archives"
        if not archives.exists():
            return []

        return [
            file.name.replace(".tar.zst.age", "")
            for file in sorted(archives.glob("*.tar.zst.age"))
        ]

    def _copy(
        self,
        src: Path,
        dst: Path,
    ):
        if src.resolve() == dst.resolve():
            return

        shutil.copy2(src, dst)
