"""The record of what the cloud copy is supposed to contain.

A bucket listing only says what IS there, never what SHOULD be, so a failed
upload or a credential pointing at an empty bucket both look healthy. Every
sync appends here instead: backup id, size, SHA-256, where it reached.
Checking the cloud then means comparing it against this.

Kept in three places — the server, the bucket, and the office drive.

Not a security control: anyone who can delete an archive can edit this too.
It catches accident and misconfiguration. Object Lock is the other problem.
"""

from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

LEDGER_NAME = "ledger.json"
ARCHIVE_SUFFIX = ".tar.zst.age"

# Bump when the on-disk shape changes in a way older readers cannot handle.
LEDGER_VERSION = 1


@dataclass
class Discrepancy:
    kind: str          # missing | size | unexpected
    backup_id: str
    detail: str

    def __str__(self) -> str:
        return f"{self.backup_id}: {self.detail}"


@dataclass
class CheckResult:
    reachable: bool
    expected: int = 0
    found: int = 0
    missing: list[Discrepancy] = field(default_factory=list)
    mismatched: list[Discrepancy] = field(default_factory=list)
    unexpected: list[Discrepancy] = field(default_factory=list)
    error: str | None = None

    @property
    def healthy(self) -> bool:
        return (self.reachable and not self.error
                and not self.missing and not self.mismatched)

    @property
    def summary(self) -> str:
        if not self.reachable:
            return f"could not reach the cloud: {self.error or 'unknown error'}"
        if self.expected == 0:
            return "nothing has been recorded as uploaded yet"
        if self.healthy and not self.unexpected:
            noun = "archive" if self.expected == 1 else "archives"
            return (f"the cloud holds all {self.expected} {noun} the ledger "
                    f"expects")
        parts = []
        if self.missing:
            parts.append(f"{len(self.missing)} missing")
        if self.mismatched:
            parts.append(f"{len(self.mismatched)} the wrong size")
        if self.unexpected:
            parts.append(f"{len(self.unexpected)} not in the ledger")
        return f"{self.expected} expected — " + ", ".join(parts)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def path_for(repository_root: Path) -> Path:
    return Path(repository_root) / LEDGER_NAME


def load(repository_root: Path) -> dict:
    """The ledger as it stands, or an empty one."""
    path = path_for(repository_root)
    if not path.exists():
        return {"version": LEDGER_VERSION, "created": _now(), "backups": {}}
    try:
        data = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        return {"version": LEDGER_VERSION, "created": _now(), "backups": {}}

    # Valid JSON of the wrong shape is the awkward case: it survives parsing
    # and then fails much later, where the error says nothing useful. A
    # ledger we cannot trust is treated as no ledger.
    if not isinstance(data, dict):
        return {"version": LEDGER_VERSION, "created": _now(), "backups": {}}
    if not isinstance(data.get("backups"), dict):
        data["backups"] = {}
    data["backups"] = {
        str(key): value
        for key, value in data["backups"].items()
        if isinstance(value, dict)
    }
    data.setdefault("version", LEDGER_VERSION)
    return data


def save(repository_root: Path, data: dict) -> Path:
    path = path_for(repository_root)
    path.parent.mkdir(parents=True, exist_ok=True)
    data["updated"] = _now()
    data["backup_count"] = len(data.get("backups", {}))
    data["total_bytes"] = sum(
        entry.get("bytes", 0) for entry in data.get("backups", {}).values()
    )
    path.write_text(json.dumps(data, indent=2, sort_keys=True))
    return path


def record(repository_root: Path, artifact, destinations: dict) -> dict:
    """Note that one backup was stored, and where it reached.

    `destinations` is {name: bool} — the same shape SyncEngine returns.
    """
    data = load(repository_root)

    try:
        digest = Path(artifact.sha256).read_text().strip().split()[0]
    except (OSError, IndexError, AttributeError):
        digest = ""

    try:
        size = Path(artifact.archive).stat().st_size
    except (OSError, AttributeError):
        size = getattr(artifact, "size", 0) or 0

    data["backups"][artifact.backup_id] = {
        "bytes": size,
        "sha256": digest,
        "recorded": _now(),
        "replicated_to": sorted(
            name for name, reached in destinations.items() if reached
        ),
    }
    save(repository_root, data)
    return data


def forget(repository_root: Path, backup_ids) -> dict:
    """Drop backups the retention policy has deliberately removed.

    Retention deleting an old archive is expected; the ledger has to be told,
    or every future check would report it missing and the real problems would
    be lost in the noise.
    """
    data = load(repository_root)
    for backup_id in backup_ids:
        data["backups"].pop(backup_id, None)
    save(repository_root, data)
    return data


def publish(repository_root: Path, remote: str) -> str | None:
    """Put the ledger in the bucket, beside what it describes."""
    if not remote:
        return "no remote configured"
    source = path_for(repository_root)
    if not source.exists():
        return "there is no ledger to publish yet"
    try:
        result = subprocess.run(
            ["rclone", "copyto", str(source), f"{remote.rstrip('/')}/{LEDGER_NAME}"],
            capture_output=True, text=True, timeout=120,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return str(exc)
    if result.returncode != 0:
        return (result.stderr or "rclone failed").strip()
    return None


def check(repository_root: Path, remote: str) -> CheckResult:
    """Compare what the cloud actually holds against what the ledger expects."""
    data = load(repository_root)
    expected = data.get("backups", {})

    if not remote:
        return CheckResult(reachable=False, error="no remote configured")

    try:
        listing = subprocess.run(
            ["rclone", "lsjson", f"{remote.rstrip('/')}/archives",
             "--files-only", "--no-modtime"],
            capture_output=True, text=True, timeout=180,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return CheckResult(reachable=False, error=str(exc))

    if listing.returncode != 0:
        message = (listing.stderr or "").strip()
        # An empty or brand-new bucket is reachable, just empty. That is a
        # meaningful answer, not a failure: it is exactly what a wrongly
        # repointed credential looks like.
        if "directory not found" in message.lower():
            found = {}
        else:
            return CheckResult(reachable=False, error=message or "rclone failed")
    else:
        try:
            entries = json.loads(listing.stdout or "[]")
        except json.JSONDecodeError:
            return CheckResult(reachable=False, error="could not read the listing")
        found = {
            entry["Name"][: -len(ARCHIVE_SUFFIX)]: entry.get("Size", 0)
            for entry in entries
            if entry.get("Name", "").endswith(ARCHIVE_SUFFIX)
        }

    result = CheckResult(
        reachable=True, expected=len(expected), found=len(found)
    )

    for backup_id, entry in sorted(expected.items()):
        if backup_id not in found:
            result.missing.append(Discrepancy(
                "missing", backup_id,
                "in the ledger but not in the cloud",
            ))
            continue
        wanted = entry.get("bytes", 0)
        actual = found[backup_id]
        if wanted and actual != wanted:
            result.mismatched.append(Discrepancy(
                "size", backup_id,
                f"cloud has {actual:,} bytes, ledger expects {wanted:,}",
            ))

    for backup_id in sorted(set(found) - set(expected)):
        result.unexpected.append(Discrepancy(
            "unexpected", backup_id,
            "in the cloud but not in the ledger",
        ))

    return result
