import hashlib

from storage.artifact import BackupArtifact


def sha256_file(path):

    h = hashlib.sha256()

    with open(path, "rb") as f:

        while True:

            chunk = f.read(8192)

            if not chunk:
                break

            h.update(chunk)

    return h.hexdigest()


def verify_archive(
    artifact: BackupArtifact,
):

    digest = sha256_file(
        artifact.archive
    )

    expected = (
        artifact.sha256
        .read_text()
        .strip()
    )

    if digest != expected:

        raise RuntimeError(
            "Archive verification failed"
        )

    return True