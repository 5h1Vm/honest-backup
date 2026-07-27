"""
Retention: how long backups are kept, per destination.

Each destination has its own independent window, configured in
backup.conf and editable from the TUI:

    LOCAL_RETENTION_DAYS       archives in the local vault on this machine
    BACKBLAZE_RETENTION_DAYS   archives in the Backblaze bucket
    USB_RETENTION_DAYS         archives on the USB/HDD copy

A value of 0 (or "forever") means never delete from that destination.
This is what makes tiered retention possible: keep a short window on the
VM's disk while the cloud and the external drive keep a longer history.

A backup is identified by its ID (YYYY-MM-DD_HH-MM-SS) and consists of
three files that are always pruned together:

    archives/<id>.tar.zst.age
    hashes/<id>.sha256
    manifests/<id>.manifest.json

Safety rules that apply to every destination:
  - The most recent backup is never deleted, whatever the setting says.
  - Nothing is deleted from the local vault unless that same backup
    exists on at least one other destination that is enabled, or the
    local vault is the only destination configured.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path

from storage.config import (
    REPOSITORY_PATH,
    BACKBLAZE_ENABLED,
    BACKBLAZE_REMOTE,
    USB_ENABLED,
    USB_LABEL,
    USB_BACKUP_PATH,
)
from storage.mount import find_mountpoint
from storage.rclone import Rclone

from .config import CFG


BACKUP_ID_RE = re.compile(r"^\d{4}-\d{2}-\d{2}_\d{2}-\d{2}-\d{2}$")

ARCHIVE_SUFFIX = ".tar.zst.age"


def _days(key: str) -> int:
    """Retention window in days; 0 means keep forever."""
    raw = str(CFG.get(key, "0")).strip().lower()
    if raw in ("", "0", "forever", "never", "keep", "unlimited"):
        return 0
    try:
        value = int(raw)
    except ValueError:
        return 0
    return max(value, 0)


def local_days() -> int:
    return _days("LOCAL_RETENTION_DAYS")


def backblaze_days() -> int:
    return _days("BACKBLAZE_RETENTION_DAYS")


def usb_days() -> int:
    return _days("USB_RETENTION_DAYS")


def backup_id_date(backup_id: str) -> datetime | None:
    try:
        return datetime.strptime(backup_id, "%Y-%m-%d_%H-%M-%S")
    except ValueError:
        return None


def member_files(backup_id: str) -> list[str]:
    """The three files that make up one backup, relative to a vault root."""
    return [
        f"archives/{backup_id}{ARCHIVE_SUFFIX}",
        f"hashes/{backup_id}.sha256",
        f"manifests/{backup_id}.manifest.json",
    ]


@dataclass
class DestinationResult:
    name: str
    enabled: bool = False
    reachable: bool = False
    days: int = 0
    kept: int = 0
    deleted: list[str] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)
    error: str | None = None

    @property
    def summary(self) -> str:
        if not self.enabled:
            return f"{self.name}: not enabled"
        if self.error:
            return f"{self.name}: {self.error}"
        if not self.reachable:
            return f"{self.name}: not reachable"
        if self.days == 0:
            return f"{self.name}: keeping everything ({self.kept} backups)"
        return (
            f"{self.name}: keeping {self.days} days — "
            f"{self.kept} kept, {len(self.deleted)} removed"
        )


# ----------------------------------------------------------------------
# Listing
# ----------------------------------------------------------------------
def list_local_ids(vault: Path | None = None) -> list[str]:
    root = Path(vault) if vault else Path(REPOSITORY_PATH)
    archives = root / "archives"
    if not archives.exists():
        return []
    ids = [
        file.name[: -len(ARCHIVE_SUFFIX)]
        for file in archives.glob(f"*{ARCHIVE_SUFFIX}")
    ]
    return sorted(i for i in ids if BACKUP_ID_RE.match(i))


def list_remote_ids(remote: str) -> list[str]:
    """Backup IDs present at an rclone remote (or any rclone-addressable path)."""
    try:
        listing = Rclone.ls(f"{remote}/archives")
    except RuntimeError:
        return []
    ids: list[str] = []
    for line in listing.splitlines():
        line = line.strip()
        if not line.endswith(ARCHIVE_SUFFIX):
            continue
        name = line.split(maxsplit=1)[-1]
        backup_id = Path(name).name[: -len(ARCHIVE_SUFFIX)]
        if BACKUP_ID_RE.match(backup_id):
            ids.append(backup_id)
    return sorted(ids)


def usb_destination() -> Path | None:
    mount = find_mountpoint(USB_LABEL)
    if mount is None:
        return None
    return mount / USB_BACKUP_PATH if USB_BACKUP_PATH else mount


# ----------------------------------------------------------------------
# Deciding what to drop
# ----------------------------------------------------------------------
def expired(ids: list[str], days: int, now: datetime | None = None) -> list[str]:
    """IDs older than the window. The newest backup is always kept."""
    if days <= 0 or not ids:
        return []
    now = now or datetime.now()
    cutoff = now - timedelta(days=days)
    ordered = sorted(ids)
    newest = ordered[-1]
    out = []
    for backup_id in ordered:
        if backup_id == newest:
            continue
        moment = backup_id_date(backup_id)
        if moment and moment < cutoff:
            out.append(backup_id)
    return out


# ----------------------------------------------------------------------
# Applying retention
# ----------------------------------------------------------------------
def prune_local(dry_run: bool = False, now: datetime | None = None) -> DestinationResult:
    """Delete expired archives from this machine's vault.

    A local file is only deleted when a copy exists somewhere else
    (Backblaze or USB) — unless no other destination is enabled at all.
    """
    result = DestinationResult(name="This computer", enabled=True, reachable=True,
                               days=local_days())
    root = Path(REPOSITORY_PATH)
    ids = list_local_ids(root)
    result.kept = len(ids)
    if result.days == 0:
        return result

    candidates = expired(ids, result.days, now)
    if not candidates:
        return result

    elsewhere: set[str] = set()
    other_destination = False
    if BACKBLAZE_ENABLED:
        other_destination = True
        elsewhere.update(list_remote_ids(BACKBLAZE_REMOTE))
    if USB_ENABLED:
        destination = usb_destination()
        if destination is not None:
            other_destination = True
            elsewhere.update(list_remote_ids(str(destination)))

    for backup_id in candidates:
        if other_destination and backup_id not in elsewhere:
            result.skipped.append(backup_id)
            continue
        if not dry_run:
            for relative in member_files(backup_id):
                try:
                    (root / relative).unlink(missing_ok=True)
                except OSError as exc:
                    result.error = str(exc)
        result.deleted.append(backup_id)

    result.kept = len(ids) - len(result.deleted)
    return result


def _prune_remote(
    name: str,
    enabled: bool,
    remote: str | None,
    days: int,
    dry_run: bool,
    now: datetime | None,
) -> DestinationResult:
    result = DestinationResult(name=name, enabled=enabled, days=days)
    if not enabled or remote is None:
        return result
    ids = list_remote_ids(remote)
    result.reachable = True
    result.kept = len(ids)
    if days == 0 or not ids:
        return result
    for backup_id in expired(ids, days, now):
        if not dry_run:
            for relative in member_files(backup_id):
                try:
                    Rclone.delete(f"{remote}/{relative}")
                except RuntimeError as exc:
                    result.error = str(exc).strip().splitlines()[-1][:200]
                    continue
        result.deleted.append(backup_id)
    result.kept = len(ids) - len(result.deleted)
    return result


def prune_backblaze(dry_run: bool = False, now: datetime | None = None) -> DestinationResult:
    return _prune_remote(
        "Backblaze", BACKBLAZE_ENABLED, BACKBLAZE_REMOTE if BACKBLAZE_ENABLED else None,
        backblaze_days(), dry_run, now,
    )


def prune_usb(dry_run: bool = False, now: datetime | None = None) -> DestinationResult:
    destination = usb_destination() if USB_ENABLED else None
    result = _prune_remote(
        "USB / external drive", USB_ENABLED,
        str(destination) if destination else None,
        usb_days(), dry_run, now,
    )
    if USB_ENABLED and destination is None:
        result.error = "drive not connected"
    return result


def apply_retention(dry_run: bool = False, now: datetime | None = None) -> list[DestinationResult]:
    """Enforce every destination's retention window. Cloud and USB first,
    so the local check can still see what exists elsewhere."""
    results = [
        prune_backblaze(dry_run, now),
        prune_usb(dry_run, now),
        prune_local(dry_run, now),
    ]

    # Retention removing an old archive from the cloud is deliberate, so the
    # ledger has to be told. Otherwise every later check would report it
    # missing, and real problems would be lost among expected ones.
    if not dry_run:
        try:
            from storage import ledger

            removed = [
                backup_id
                for destination in results
                if destination.name.lower().startswith("backblaze")
                for backup_id in destination.deleted
            ]
            if removed:
                ledger.forget(Path(REPOSITORY_PATH), removed)
        except Exception:
            # A ledger that is briefly out of step is far better than a
            # retention pass that aborts halfway through.
            pass

    return results
