import hashlib
import subprocess

from pathlib import Path
from datetime import datetime

from storage.artifact import BackupArtifact

from .config import (
    WORKSPACE,
    ARCHIVE_PUBLIC_KEY,
)


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()

    with open(path, "rb") as f:
        while True:
            chunk = f.read(1024 * 1024)
            if not chunk:
                break

            h.update(chunk)

    return h.hexdigest()


def build_archive(
    backup_id: str,
    day: str,
    manifest=None,
    report=None,
):
    workspace = WORKSPACE / day

    archive_dir = WORKSPACE / "archive"

    archive_dir.mkdir(
        parents=True,
        exist_ok=True,
    )

    tar_file = archive_dir / f"{backup_id}.tar"

    subprocess.run(
        [
            "tar",
            "-cf",
            str(tar_file),
            "-C",
            str(WORKSPACE),
            day,
        ],
        check=True,
    )

    subprocess.run(
        [
            "zstd",
            "-f",
            str(tar_file),
        ],
        check=True,
    )

    zst_file = Path(str(tar_file) + ".zst")

    encrypted = Path(str(zst_file) + ".age")

    public_key = ARCHIVE_PUBLIC_KEY.read_text().strip()

    subprocess.run(
        [
            "age",
            "-r",
            public_key,
            "-o",
            str(encrypted),
            str(zst_file),
        ],
        check=True,
    )

    digest = sha256_file(encrypted)

    hash_file = archive_dir / f"{backup_id}.sha256"

    hash_file.write_text(digest)

    tar_file.unlink()

    zst_file.unlink()

    artifact = BackupArtifact(
        backup_id=backup_id,
        created=datetime.now(),
        archive=encrypted,
        sha256=hash_file,
        manifest=manifest,
        report=report,
        size=encrypted.stat().st_size,
    )

    return artifact
