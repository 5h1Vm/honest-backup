#!/usr/bin/env python3
"""Push the office drive's history back up to a cloud bucket.

The mirror image of pull.py, and the reason the drive is worth keeping.

Use it when the cloud side has been rebuilt and no longer holds the full
history — a new Backblaze account, a new bucket, a move to a different
provider, or a bucket that was emptied. The drive is the older, longer copy,
so the drive is the source of truth and the cloud is refilled from it.

    python reseed.py --target backblaze-new:your-bucket
    python reseed.py --target backblaze-new:your-bucket --dry-run

Rotating a Backblaze application key is NOT one of these cases. A key is
permission to reach the data, not the data itself: update the rclone remote
on both machines and every archive is exactly where it was. Nothing needs
moving and this script has no part to play.

Like pull.py, this only ever copies. It never deletes from either side, so
running it against a bucket that already holds some of the history simply
fills in what is missing.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from pull import (  # noqa: E402  - deliberately reusing the sibling script
    FOLDERS, ARCHIVE_SUFFIX, DEFAULT_CONF,
    load_conf, settings, rclone, remote_listing, local_backups,
    verify, human, say, die,
)


def run(args) -> int:
    conf = load_conf(Path(args.config))
    config = settings(conf)

    source = config["destination"]
    if not source:
        die(f"DESTINATION is not set in {args.config}")
    if not source.exists():
        die(f"{source} is not reachable. Is the drive plugged in?")

    target = args.target.rstrip("/")

    held = local_backups(source)
    if not held:
        die(f"{source} holds no archives — there is nothing to push up.")

    already = set()
    try:
        for item in remote_listing(target, "archives"):
            name = item.get("Name", "")
            if name.endswith(ARCHIVE_SUFFIX):
                already.add(name[: -len(ARCHIVE_SUFFIX)])
    except RuntimeError as e:
        die(f"could not read {target}: {e}")

    missing = sorted(set(held) - already)
    total_bytes = sum(held[i]["bytes"] for i in missing)

    say()
    say("  HonestBackup — refill the cloud from the office drive")
    say(f"  drive:   {source}  ({len(held)} backups)")
    say(f"  target:  {target}  ({len(already)} backups)")
    say()

    if not missing:
        say("  The target already holds everything this drive holds.")
        say("  Nothing to do.")
        say()
        return 0

    say(f"  To upload: {len(missing)} backups, {human(total_bytes)}")
    say(f"  Oldest missing: {missing[0]}")
    say(f"  Newest missing: {missing[-1]}")
    say()

    # Never push a damaged archive up as though it were good. The drive is
    # being treated as the authority here, so it has to earn that.
    if not args.skip_verify:
        say(f"  Verifying the {len(missing)} archives before upload")
        broken = []
        for backup_id in missing:
            state, detail = verify(backup_id, held[backup_id])
            if state == "ok":
                continue
            broken.append((backup_id, state, detail))
            say(f"    {backup_id}  {state.upper()}  {detail}")
        if broken:
            say()
            say(f"  {len(broken)} archive(s) did not verify and will not be "
                f"uploaded as authoritative copies.")
            say(f"  Fix or remove them, or re-run with --skip-verify if you "
                f"understand the risk.")
            say()
            return 2
        say("  All verified.")
        say()

    if args.dry_run:
        say("  --dry-run: nothing was uploaded.")
        say()
        return 0

    if not args.yes:
        answer = input(f"  Upload {len(missing)} backups to {target}? [y/N] ")
        if answer.strip().lower() not in ("y", "yes"):
            say("  Cancelled. Nothing was uploaded.")
            return 1
        say()

    for folder in FOLDERS:
        local_folder = source / folder
        if not local_folder.exists():
            continue
        say(f"  {folder} …")
        rclone([
            "copy", str(local_folder), f"{target}/{folder}",
            "--transfers", config["transfers"],
            "--checksum", "--progress", "--stats-one-line",
        ] + (["--bwlimit", config["bandwidth"]] if config["bandwidth"] else []),
            capture=False)

    say()
    say(f"  Done. {target} now holds the drive's full history.")
    say(f"  Point BACKBLAZE_REMOTE in the server's config/backup.conf at "
        f"{target} and the next backup will continue into it.")
    say()
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Refill a cloud bucket from the office drive's copy.")
    parser.add_argument("--target", required=True,
                        help="rclone remote and path to upload to, "
                             "e.g. backblaze-new:your-bucket")
    parser.add_argument("--config", default=str(DEFAULT_CONF),
                        help="configuration file (default: pull.conf)")
    parser.add_argument("--dry-run", action="store_true",
                        help="show what would be uploaded, upload nothing")
    parser.add_argument("--skip-verify", action="store_true",
                        help="do not checksum the archives before uploading")
    parser.add_argument("--yes", action="store_true",
                        help="do not ask for confirmation")
    args = parser.parse_args()

    try:
        return run(args)
    except KeyboardInterrupt:
        say("\n  Stopped by the user.")
        return 130
    except RuntimeError as e:
        die(str(e))
    return 1


if __name__ == "__main__":
    sys.exit(main())
