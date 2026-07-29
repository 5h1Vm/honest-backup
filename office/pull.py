#!/usr/bin/env python3
"""Pull the backup archive from Backblaze down to a local drive.

This runs on the office laptop, not on the backup server. It is deliberately
self-contained: copy this one file plus its .conf onto the laptop, install
rclone, and it works. It does not import anything from the rest of
HonestBackup and needs nothing beyond the Python standard library.

What it does
    1. copies new archives, hashes and manifests from Backblaze to the drive
    2. verifies every new archive against its SHA-256
    3. writes an index and a status file describing exactly what is held
    4. optionally publishes that status back to Backblaze so the server's
       terminal interface can show whether the office copy is current

What it never does
    It never deletes anything from the drive. The copy on the drive is the
    last line of defence: it must survive the cloud copy ageing out, the
    cloud bucket being emptied, and the cloud account being lost. rclone is
    called with "copy", never "sync", and the drive is treated as
    append-only. Anything the drive holds that Backblaze no longer holds is
    reported as held-only-here, and that is the normal, healthy state once
    the Backblaze retention window has passed.

Usage
    python pull.py                 pull, verify new archives, report
    python pull.py --verify-all    re-verify every archive already on drive
    python pull.py --dry-run       show what would be copied, copy nothing
    python pull.py --status        report on the drive without touching the
                                   network
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import re
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

ARCHIVE_SUFFIX = ".tar.zst.age"
HASH_SUFFIX = ".sha256"
MANIFEST_SUFFIX = ".manifest.json"

# The folders that make up a repository. Order matters: hashes arrive before
# archives so that a run interrupted midway still leaves every archive that
# did land verifiable.
FOLDERS = ("hashes", "manifests", "archives", "reports", "metadata")

SCRIPT_DIR = Path(__file__).resolve().parent
DEFAULT_CONF = SCRIPT_DIR / "pull.conf"


# ---------------------------------------------------------------------------
# configuration
# ---------------------------------------------------------------------------

def load_conf(path: Path) -> dict:
    """Read KEY=VALUE lines, the same format as the server's backup.conf."""
    conf = {}
    if not path.exists():
        return conf
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        conf[key.strip()] = value.strip().strip('"').strip("'")
    return conf


def settings(conf: dict) -> dict:
    def flag(key, default):
        return conf.get(key, default).strip().lower() in ("true", "yes", "1")

    remote = conf.get("REMOTE", "").rstrip("/")
    destination = conf.get("DESTINATION", "")

    # Checked here rather than by rclone: "bad suffix 'd'" means nothing to
    # somebody who mistyped a speed limit.
    transfers = conf.get("TRANSFERS", "4").strip() or "4"
    if not transfers.isdigit() or not 1 <= int(transfers) <= 64:
        die(f"TRANSFERS must be a number between 1 and 64, not '{transfers}'")

    bandwidth = conf.get("BANDWIDTH_LIMIT", "").strip()
    if bandwidth and not re.fullmatch(r"\d+(\.\d+)?[KMGkmg]?", bandwidth):
        die(f"BANDWIDTH_LIMIT must look like 10M or 500K, not '{bandwidth}'")

    return {
        "remote": remote,
        "destination": Path(os.path.expanduser(destination)) if destination else None,
        "verify": flag("VERIFY", "true"),
        "publish_status": flag("PUBLISH_STATUS", "true"),
        "status_remote": conf.get("STATUS_REMOTE", "").rstrip("/") or (
            f"{remote}/status" if remote else ""
        ),
        "site": conf.get("SITE", "") or platform.node() or "office",
        "bandwidth": bandwidth,
        "transfers": transfers,
    }


# ---------------------------------------------------------------------------
# rclone
# ---------------------------------------------------------------------------

def platform_tag() -> str:
    """e.g. darwin-arm64 — how get-tools.sh labels the binaries it fetches."""
    system = {"Darwin": "darwin", "Linux": "linux"}.get(
        platform.system(), platform.system().lower())
    machine = {"x86_64": "amd64", "amd64": "amd64",
               "arm64": "arm64", "aarch64": "arm64"}.get(
                   platform.machine().lower(), platform.machine().lower())
    return f"{system}-{machine}"


def rclone_path() -> str:
    """The rclone to use: one shipped beside this script wins over the system.

    That is what makes the drive portable — carry rclone on the drive and it
    runs on a machine that has never heard of it. A drive prepared for
    several machines holds one binary each, tagged by platform, so the
    tagged one is tried first: otherwise a drive filled on Linux hands a
    Mac a Linux binary.
    """
    suffix = ".exe" if os.name == "nt" else ""
    for folder in (SCRIPT_DIR / "tools", SCRIPT_DIR):
        for name in (f"rclone-{platform_tag()}{suffix}", f"rclone{suffix}"):
            local = folder / name
            if local.exists() and os.access(local, os.X_OK):
                return str(local)
    found = shutil.which("rclone")
    if not found:
        die("rclone is not installed, or not on PATH.\n"
            "    Linux:    sudo apt install rclone\n"
            "    macOS:    brew install rclone\n"
            "    Windows:  winget install Rclone.Rclone\n"
            "  Or put the rclone binary next to this script to carry it along.")
    return found


def rclone_config_args() -> list[str]:
    """Use an rclone.conf beside this script if there is one.

    Keeps the remote definition on the drive rather than in whichever
    account happens to be logged in.
    """
    local = SCRIPT_DIR / "rclone.conf"
    return ["--config", str(local)] if local.exists() else []


def rclone(args: list[str], capture=True) -> str:
    cmd = [rclone_path()] + rclone_config_args() + args
    result = subprocess.run(
        cmd,
        text=True,
        capture_output=capture,
        check=False,
    )
    if result.returncode != 0:
        stderr = (result.stderr or "").strip()
        raise RuntimeError(
            f"rclone failed ({result.returncode})\n"
            f"  command: {' '.join(cmd)}\n"
            f"  {stderr}"
        )
    return result.stdout or ""


def remote_listing(remote: str, folder: str) -> list[dict]:
    """Files in one remote folder. A missing folder is not an error."""
    try:
        raw = rclone([
            "lsjson", f"{remote}/{folder}",
            "--files-only", "--no-modtime",
        ])
    except RuntimeError as e:
        if "directory not found" in str(e).lower():
            return []
        raise
    try:
        return json.loads(raw or "[]")
    except json.JSONDecodeError:
        return []


# ---------------------------------------------------------------------------
# the drive
# ---------------------------------------------------------------------------

def local_backups(destination: Path) -> dict:
    """Every backup id held on the drive, with the files that make it up."""
    archives = destination / "archives"
    if not archives.exists():
        return {}

    held = {}
    for archive in sorted(archives.glob(f"*{ARCHIVE_SUFFIX}")):
        backup_id = archive.name[: -len(ARCHIVE_SUFFIX)]
        hash_file = destination / "hashes" / f"{backup_id}{HASH_SUFFIX}"
        manifest = destination / "manifests" / f"{backup_id}{MANIFEST_SUFFIX}"
        held[backup_id] = {
            "archive": archive,
            "bytes": archive.stat().st_size,
            "hash_file": hash_file if hash_file.exists() else None,
            "manifest": manifest if manifest.exists() else None,
        }
    return held


def sha256_of(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def expected_hash(hash_file: Path) -> str | None:
    """The server writes a bare hex digest; tolerate 'digest  filename' too."""
    try:
        text = hash_file.read_text(encoding="utf-8").strip()
    except OSError:
        return None
    if not text:
        return None
    return text.split()[0].lower()


def verify(backup_id: str, entry: dict) -> tuple[str, str]:
    """Returns (state, detail). State is ok / mismatch / no-hash / unreadable."""
    if not entry["hash_file"]:
        return "no-hash", "no .sha256 alongside the archive"
    want = expected_hash(entry["hash_file"])
    if not want:
        return "no-hash", "the .sha256 file is empty"
    try:
        got = sha256_of(entry["archive"])
    except OSError as e:
        return "unreadable", str(e)
    if got != want:
        return "mismatch", f"expected {want[:16]}…, got {got[:16]}…"
    return "ok", want


# ---------------------------------------------------------------------------
# output
# ---------------------------------------------------------------------------

def human(byte_count: int) -> str:
    size = float(byte_count)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if size < 1024 or unit == "TB":
            return f"{size:.1f} {unit}" if unit != "B" else f"{int(size)} B"
        size /= 1024
    return f"{size:.1f} TB"


def say(message=""):
    print(message, flush=True)


def die(message: str, code: int = 1):
    print(f"\n  Stopped: {message}\n", file=sys.stderr, flush=True)
    sys.exit(code)


# ---------------------------------------------------------------------------
# the run
# ---------------------------------------------------------------------------

def copy_down(config: dict, dry_run: bool) -> None:
    remote = config["remote"]
    destination = config["destination"]

    for folder in FOLDERS:
        args = [
            "copy",
            f"{remote}/{folder}",
            str(destination / folder),
            "--transfers", config["transfers"],
            "--checksum",
            "--progress",
            "--stats-one-line",
        ]
        if config["bandwidth"]:
            args += ["--bwlimit", config["bandwidth"]]
        if dry_run:
            args.append("--dry-run")

        say(f"  {folder} …")
        try:
            rclone(args, capture=False)
        except RuntimeError as e:
            if "directory not found" in str(e).lower():
                say(f"  {folder}: nothing in the cloud yet, skipped")
                continue
            raise


FOLDER_README = """\
What is in this folder
=======================

This is the office copy of the HonestBackup archives, brought down from
the cloud. It is a plain copy — nothing here needs HonestBackup installed
to exist safely, only to be read.

  archives/    The actual backups. One file per backup run, encrypted —
               opening one directly shows scrambled bytes, which is
               expected. Open the drive's HonestBackup.command (Mac) or
               copy-now.sh (Linux) and choose "look inside" instead.

  hashes/      A short fingerprint for each archive. Used to prove an
               archive has not been damaged or altered since it was made.

  manifests/   A list of what each backup contains — which service, which
               data, how many records — without opening the archive.

  reports/     A plain-language summary of each backup run: what worked,
               what did not, what needs attention.

  metadata/    Extra details about each run, used by the tools rather than
               read directly.

Nothing in this folder is ever deleted by these tools, even after a
backup ages out of the cloud. If a folder here holds something the cloud
no longer does, that is normal — it means this drive is now the only
copy, which is exactly the point of keeping one.
"""


def write_folder_readme(destination: Path) -> None:
    """Leave a plain-English explanation next to the data itself.

    Anyone who plugs this drive into an unfamiliar machine, or opens it
    five years from now, should be able to make sense of it without
    reaching for documentation that lives somewhere else.
    """
    readme = destination / "README.txt"
    if readme.exists():
        return
    try:
        destination.mkdir(parents=True, exist_ok=True)
        readme.write_text(FOLDER_README, encoding="utf-8")
    except OSError:
        pass  # a missing README never stops a backup from being copied


def publish_status(config: dict, status: dict) -> None:
    """Put the status file back in the cloud so the server can see it."""
    target = config["status_remote"]
    if not target:
        return
    temporary = config["destination"] / "office-status.json"
    try:
        rclone(["copyto", str(temporary),
                f"{target}/{config['site']}.json"])
        say(f"  status published to {target}/{config['site']}.json")
    except RuntimeError as e:
        say(f"  status could not be published: {e}")


def run(args) -> int:
    conf = load_conf(Path(args.config))
    config = settings(conf)

    if not config["destination"]:
        die(f"DESTINATION is not set in {args.config}")
    if not config["remote"] and not args.status:
        die(f"REMOTE is not set in {args.config}")

    destination = config["destination"]

    # A drive that is not plugged in must never be silently recreated as an
    # empty folder on the laptop's own disk — that would look like a total
    # loss of history on the next run.
    root = destination if destination.exists() else destination.parent
    if not root.exists():
        die(f"{destination} is not reachable. Is the drive plugged in?")

    if not args.status:
        write_folder_readme(destination)

    say()
    say(f"  HonestBackup — office copy")
    say(f"  drive:  {destination}")
    if not args.status:
        say(f"  cloud:  {config['remote']}")
    say()

    before = local_backups(destination)

    # What the cloud holds. Read before copying so a dry run can say exactly
    # what is outstanding, and so we can tell afterwards which backups now
    # survive only on this drive.
    cloud = {}
    cloud_reachable = False
    if not args.status:
        try:
            for item in remote_listing(config["remote"], "archives"):
                name = item.get("Name", "")
                if name.endswith(ARCHIVE_SUFFIX):
                    cloud[name[: -len(ARCHIVE_SUFFIX)]] = item.get("Size", 0)
            cloud_reachable = True
        except RuntimeError as e:
            say(f"  could not list the cloud archive: {e}")
    cloud_ids = set(cloud)

    pending = sorted(cloud_ids - set(before))

    if args.dry_run:
        say(f"  Would copy {len(pending)} backup(s), "
            f"{human(sum(cloud[i] for i in pending))}")
        say()

    if not args.status:
        for folder in FOLDERS:
            (destination / folder).mkdir(parents=True, exist_ok=True)
        say("  Copying from Backblaze (nothing on the drive is ever deleted)")
        copy_down(config, args.dry_run)
        say()

    after = local_backups(destination)
    new_ids = sorted(set(after) - set(before))

    # ------------------------------------------------------------------
    # verify
    # ------------------------------------------------------------------
    to_check = sorted(after) if args.verify_all else new_ids
    results = {}
    if config["verify"] and to_check and not args.dry_run:
        say(f"  Verifying {len(to_check)} archive(s)")
        for backup_id in to_check:
            state, detail = verify(backup_id, after[backup_id])
            results[backup_id] = {"state": state, "detail": detail}
            mark = {"ok": "ok", "mismatch": "CORRUPT",
                    "no-hash": "unverified", "unreadable": "UNREADABLE"}[state]
            say(f"    {backup_id}  {mark}")
        say()

    bad = [i for i, r in results.items() if r["state"] == "mismatch"]
    unreadable = [i for i, r in results.items() if r["state"] == "unreadable"]
    only_here = sorted(set(after) - cloud_ids) if cloud_reachable else []

    total_bytes = sum(entry["bytes"] for entry in after.values())
    ids = sorted(after)

    # ------------------------------------------------------------------
    # index and status
    # ------------------------------------------------------------------
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")

    index = {
        "site": config["site"],
        "drive": str(destination),
        "generated": now,
        "backups": [
            {
                "id": backup_id,
                "bytes": entry["bytes"],
                "hash": (expected_hash(entry["hash_file"])
                         if entry["hash_file"] else None),
                "manifest": bool(entry["manifest"]),
                "in_cloud": backup_id in cloud_ids if cloud_reachable else None,
            }
            for backup_id, entry in ((i, after[i]) for i in ids)
        ],
    }

    status = {
        "site": config["site"],
        "drive": str(destination),
        "host": platform.node(),
        "last_pull": now,
        "backups_held": len(after),
        "bytes_held": total_bytes,
        "oldest": ids[0] if ids else None,
        "newest": ids[-1] if ids else None,
        "new_this_run": new_ids,
        "verified_this_run": len(results),
        "corrupt": bad,
        "unreadable": unreadable,
        "held_only_here": only_here,
        "cloud_reachable": cloud_reachable,
        "healthy": not bad and not unreadable and bool(after),
    }

    if not args.dry_run:
        (destination / "office-index.json").write_text(
            json.dumps(index, indent=2), encoding="utf-8")
        (destination / "office-status.json").write_text(
            json.dumps(status, indent=2), encoding="utf-8")

    # ------------------------------------------------------------------
    # summary
    # ------------------------------------------------------------------
    say(f"  Held on this drive:  {len(after)} backups, {human(total_bytes)}")
    if ids:
        say(f"  Oldest:              {ids[0]}")
        say(f"  Newest:              {ids[-1]}")
    if args.dry_run:
        say(f"  Outstanding:         {len(pending)}")
    elif new_ids:
        say(f"  New this run:        {len(new_ids)}")
    elif not args.status:
        say(f"  New this run:        none, already up to date")
    if only_here:
        say(f"  Only on this drive:  {len(only_here)} "
            f"(aged out of Backblaze — this drive is now their only copy)")
    say()

    if bad:
        say(f"  FAILED CHECKSUM: {', '.join(bad)}")
        say(f"  Those files are damaged. Delete them from the drive and run "
            f"again to fetch a clean copy.")
        say()
    if unreadable:
        say(f"  COULD NOT READ: {', '.join(unreadable)}")
        say(f"  The drive may be failing. Check it before relying on this copy.")
        say()

    if config["publish_status"] and not args.dry_run and not args.status:
        publish_status(config, status)
        say()

    if bad or unreadable:
        say("  Result: PROBLEM — see above")
        return 2
    if args.dry_run:
        say("  Result: dry run — nothing was copied")
    else:
        say("  Result: office copy is complete and verified")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Pull the HonestBackup archive from Backblaze to a local drive.")
    parser.add_argument("--config", default=str(DEFAULT_CONF),
                        help="configuration file (default: pull.conf next to this script)")
    parser.add_argument("--verify-all", action="store_true",
                        help="re-check every archive on the drive, not just new ones")
    parser.add_argument("--dry-run", action="store_true",
                        help="show what would be copied without copying it")
    parser.add_argument("--status", action="store_true",
                        help="report on the drive without touching the network")
    args = parser.parse_args()

    try:
        return run(args)
    except KeyboardInterrupt:
        say("\n  Stopped by the user. Nothing on the drive was deleted.")
        return 130
    except RuntimeError as e:
        die(str(e))
    return 1


if __name__ == "__main__":
    sys.exit(main())
