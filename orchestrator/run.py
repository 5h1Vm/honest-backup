from datetime import datetime
import argparse
import sys
import os
import shutil
import subprocess
from pathlib import Path

from .report_writer import ReportWriter
from .session import BackupSession
from .scheduler import Scheduler
from .id import new_backup_id
from .config import (
    WORKSPACE,
    CFG,
)
from storage.metadata import MetadataStore
from .report import BackupReport
from .cleanup import cleanup_workspace
from .manifest import build_manifest
from .archive import build_archive
from .verify import verify_archive
from .restore import (
    list_backups,
    fetch_backup_artifact,
    verify_artifact,
    decrypt_archive,
    decompress_zstd,
    extract_tar,
    list_backup_contents,
    restore_backup,
)

from storage.repository import Repository
from storage.sync import SyncEngine

from lib.logger import Logger
from lib.alert import send_email_alert, send_telegram_alert
from lib.reporting import send_backup_report

def send_alert(subject: str, body: str):
    """Send an alert via email and/or Telegram if enabled."""
    sent_email = send_email_alert(subject, body)
    sent_telegram = send_telegram_alert(f"{subject}\n\n{body}")


def list_available_backups():
    """List all available backups in the repository."""
    print("Available backups:")
    backups = list_backups()
    if not backups:
        print("  No backups found")
        return

    for i, backup_id in enumerate(backups, 1):
        print(f"  {i}. {backup_id}")


def restore_backup_command(backup_id: str, restore_dir: str, private_key: str, files: list):
    """Handle the restore command."""
    from pathlib import Path

    restore_path = Path(restore_dir)
    print(f"Restoring backup {backup_id} to {restore_path}...")

    success = restore_backup(
        backup_id=backup_id,
        restore_dir=restore_path,
        private_key=private_key,
        selective_files=files if files else None,
    )

    if success:
        print("Restore completed successfully!")
    else:
        print("Restore failed!")
        exit(1)


def get_previous_workspace(workspace_root: Path,
                           exclude: str | None = None) -> Path | None:
    """Return the most recent YYYY-MM-DD directory under workspace_root, or None.

    `exclude` leaves one date out, which today's caller needs: today's own
    directory sorts newest of all, so a run asking "what came before me?"
    is otherwise handed itself.
    """
    try:
        dirs = [d for d in workspace_root.iterdir()
                if d.is_dir() and len(d.name) == 10 and d.name[4] == '-'
                and d.name != exclude]
        if not dirs:
            return None
        # Sort by name (lexicographic works for YYYY-MM-DD)
        dirs.sort(key=lambda d: d.name, reverse=True)
        return dirs[0]
    except Exception:
        return None


def create_incremental_workspace(workspace_root: Path, today: str) -> Path:
    """Create a new workspace directory for today using hardlinks from the most recent previous workspace.
    Returns the path to the new workspace directory."""
    new_dir = workspace_root / today

    # Yesterday's workspace has to be found *before* today's is created.
    # get_previous_workspace picks the newest dated directory it can see, and
    # today's — just made, empty — sorts newest of all. Creating it first meant
    # every run hardlinked from itself, carried nothing forward, and re-fetched
    # every file from scratch: --incremental was on and doing nothing.
    prev = get_previous_workspace(workspace_root, exclude=today)

    # A second run on the same day already holds today's fresher copy. Laying
    # yesterday's over it would undo the morning's work, so the carry-forward
    # only happens when today's workspace is genuinely new.
    #
    # "New" ignores logs/: a run that died early — before any collector wrote
    # anything — still left its log directory behind, and counting that as
    # "today already has data" made the next run inherit nothing and re-fetch
    # every file. A folder holding only the record of a failure is still empty
    # of backup.
    present = {p.name for p in new_dir.iterdir()} if new_dir.is_dir() else set()
    starting_fresh = not (present - {"logs"})

    new_dir.mkdir(parents=True, exist_ok=True)
    if prev and prev.exists() and starting_fresh:
        # Use cp -al to hardlink everything
        try:
            subprocess.run(
                ["cp", "-al", f"{prev}/.", f"{new_dir}/"],
                check=True,
                capture_output=True,
            )
        except subprocess.CalledProcessError:
            # If cp -al is unavailable (macOS), rsync does the same job.
            try:
                subprocess.run(
                    ["rsync", "-a", "--link-dest", f"{prev}/", f"{prev}/", f"{new_dir}/"],
                    check=True,
                    capture_output=True,
                )
            except Exception as exc:
                # Both ways of hardlinking failed, which on a normal disk does
                # not happen — it means the filesystem has no hardlinks at all.
                # SMB is the one that catches people out: an Azure Files share
                # mounted for the workspace supports everything else a backup
                # needs and silently not this.
                #
                # Copying instead would "work". Every run would succeed, the
                # log would still say INCREMENTAL, and every file would be
                # fetched and stored in full for ever — which is precisely the
                # failure this whole mechanism exists to prevent, arrived at
                # from underneath. A backup that quietly stops being
                # incremental is worse than one that refuses to start, because
                # nobody finds out.
                raise RuntimeError(
                    f"{new_dir} is on a filesystem without hardlinks, so "
                    f"nothing can be carried forward from {prev.name}.\n"
                    f"  Incremental backup cannot work here. Either put the "
                    f"workspace on a filesystem that supports hardlinks "
                    f"(ext4, XFS, APFS, Azure Files NFS, a managed disk), or "
                    f"turn INCREMENTAL off and accept full collection every "
                    f"run.\n"
                    f"  underlying error: {exc}"
                ) from exc
    return new_dir


def run_notion(workspace, logger):
    from collectors.notion.collector import collect
    return collect(str(workspace), logger)


def run_cloudflare(workspace, logger):
    from collectors.cloudflare.collector import collect
    return collect(str(workspace), logger)


def run_m365(workspace, logger):
    from collectors.m365.collector import collect
    return collect(str(workspace), logger)


def process_collector_result(report, name, stats):
    if not stats:
        stats = {
            "status": "failed",
            "items": {},
            "warnings": [],
            "errors": ["Collector returned no statistics"],
        }
    report.collector_finish(name, **stats)
    for warning in stats.get("warnings", []):
        report.warning(warning)
    for error in stats.get("errors", []):
        report.error(error)


def check_cloud_command() -> None:
    """Answer 'is the cloud copy complete?' against the ledger, not a guess."""
    from storage import ledger
    from storage.config import REPOSITORY_PATH, BACKBLAZE_REMOTE

    root = Path(REPOSITORY_PATH)
    record = ledger.load(root)
    result = ledger.check(root, BACKBLAZE_REMOTE)

    print()
    print(f"  Cloud:    {BACKBLAZE_REMOTE or '(not configured)'}")
    print(f"  Ledger:   {record.get('backup_count', 0)} backups, "
          f"{record.get('total_bytes', 0):,} bytes")
    print(f"  Updated:  {record.get('updated', 'never')}")
    print()
    print(f"  {result.summary}")
    print()

    for label, entries in (("Missing from the cloud", result.missing),
                           ("Wrong size", result.mismatched),
                           ("Present but not in the ledger", result.unexpected)):
        if not entries:
            continue
        print(f"  {label}:")
        for item in entries[:20]:
            print(f"    {item}")
        if len(entries) > 20:
            print(f"    … and {len(entries) - 20} more")
        print()

    if result.unexpected and not result.missing and not result.mismatched:
        print("  Archives not in the ledger are usually older backups made")
        print("  before the ledger existed. They are not a problem.")
        print()

    print("  OK" if result.healthy else "  NEEDS ATTENTION")
    print()


def main():
    parser = argparse.ArgumentParser(description="HonestBackup")
    parser.add_argument(
        "--force",
        action="store_true",
        help="Ignore collector schedules",
    )
    parser.add_argument(
        "--incremental",
        action="store_true",
        help="Enable incremental backup using hardlinks from previous run",
    )
    # Restore subcommands
    parser.add_argument(
        "--restore",
        action="store_true",
        help="Run in restore mode (list or restore backups)",
    )
    parser.add_argument(
        "--list",
        action="store_true",
        help="List available backups (requires --restore)",
    )
    parser.add_argument(
        "--backup-id",
        type=str,
        help="Backup ID to restore (requires --restore)",
    )
    parser.add_argument(
        "--restore-dir",
        type=str,
        default="./restore",
        help="Directory to restore files to (default: ./restore)",
    )
    parser.add_argument(
        "--private-key",
        type=str,
        help="Path to age private key for decryption (required for restore)",
    )
    parser.add_argument(
        "--files",
        nargs="*",
        help="Specific files to restore (if not specified, restore all)",
    )
    parser.add_argument(
        "--tui",
        action="store_true",
        help="Launch the Text User Interface (TUI)",
    )
    parser.add_argument(
        "--only",
        type=str,
        default="",
        help=(
            "Comma-separated collectors to run this time, e.g. "
            "'m365,cloudflare'. Each service keeps its own list of clock "
            "times, so cron passes in whichever ones are due. Omit to run "
            "everything that is switched on."
        ),
    )

    parser.add_argument(
        "--check-cloud",
        action="store_true",
        help="Compare what Backblaze holds against the ledger of what it "
             "should hold, and report any difference",
    )

    args = parser.parse_args()

    if args.check_cloud:
        return check_cloud_command()

    # If TUI is requested, launch it and exit
    if args.tui:
        from tui.app import HonestbackupTUI
        app = HonestbackupTUI()
        app.run()
        return

    # Handle restore mode
    if args.restore:
        if args.list:
            list_available_backups()
            return
        elif args.backup_id:
            if not args.private_key:
                print("Error: --private-key is required for restore operations")
                sys.exit(2)
            restore_backup_command(
                backup_id=args.backup_id,
                restore_dir=args.restore_dir,
                private_key=args.private_key,
                files=args.files,
            )
            return
        else:
            print("Error: Either --list or --backup-id must be specified with --restore")
            sys.exit(2)

    # Normal backup mode
    force = args.force
    incremental = args.incremental or CFG.get("INCREMENTAL", "false").lower() == "true"

    # Which collectors this invocation is for. Cron passes --only so that a
    # service scheduled three times a day is not dragged along by one
    # scheduled six times a day.
    only = {
        name.strip().lower()
        for name in args.only.split(",")
        if name.strip()
    }

    def wanted(collector: str) -> bool:
        return not only or collector in only

    backup_id = new_backup_id()
    today = backup_id[:10]

    # Collectors that keep a cumulative file also write one named for this
    # run alone. Published here rather than threaded through every collector
    # signature, since it is a fact about the run, not an argument to any
    # particular collection step.
    os.environ["HONESTBACKUP_RUN_ID"] = backup_id

    from lib.logger import display_zone

    session = BackupSession(backup_id=backup_id, started=datetime.now(display_zone()))
    executed_collectors = 0
    scheduler = Scheduler()

    # Determine workspace directory
    day_dir_candidate = WORKSPACE / today
    if incremental:
        day_dir = create_incremental_workspace(WORKSPACE, today)
    else:
        day_dir = day_dir_candidate
        day_dir.mkdir(parents=True, exist_ok=True)

    logger = Logger(day_dir / "logs", f"backup_{backup_id}.log")
    report = BackupReport()

    logger.info(f"Backup ID: {backup_id}")
    if force:
        logger.info("Running in FORCE mode")
    if incremental:
        logger.info("Running in INCREMENTAL mode (hardlinks from previous)")
    logger.info(f"Workspace: {day_dir}")

    # Microsoft 365
    if CFG.get("ENABLE_M365", "false").lower() == "true" and wanted("m365"):
        logger.section("Microsoft 365")
        report.collector_start("m365")
        try:
            stats = run_m365(day_dir / "m365", logger)
            process_collector_result(report, "m365", stats)
            executed_collectors += 1
            scheduler.mark_complete("m365")
            session.collectors.append("m365")
        except Exception as e:
            logger.error(f"M365 failed: {e}")
            report.error(str(e))
            report.collector_finish("m365", status="failed", items={}, warnings=[], errors=[str(e)])
            send_alert("M365 Collector Failed", f"The M365 collector failed with error: {e}")
        logger.end_section()

    # Cloudflare
    if CFG.get("ENABLE_CLOUDFLARE", "false").lower() == "true" and wanted("cloudflare"):
        logger.section("Cloudflare")
        report.collector_start("cloudflare")
        try:
            stats = run_cloudflare(day_dir / "cloudflare", logger)
            process_collector_result(report, "cloudflare", stats)
            executed_collectors += 1
            scheduler.mark_complete("cloudflare")
            session.collectors.append("cloudflare")
        except Exception as e:
            logger.error(f"Cloudflare failed: {e}")
            report.error(str(e))
            report.collector_finish("cloudflare", status="failed", items={}, warnings=[], errors=[str(e)])
            send_alert("Cloudflare Collector Failed", f"The Cloudflare collector failed with error: {e}")
        logger.end_section()

    # Notion
    if CFG.get("ENABLE_NOTION", "false").lower() == "true" and wanted("notion"):
        logger.section("Notion")
        report.collector_start("notion")
        try:
            stats = run_notion(day_dir / "notion", logger)
            process_collector_result(report, "notion", stats)
            executed_collectors += 1
            scheduler.mark_complete("notion")
            session.collectors.append("notion")
        except Exception as e:
            logger.error(f"Notion failed: {e}")
            report.error(str(e))
            report.collector_finish("notion", status="failed", items={}, warnings=[], errors=[str(e)])
            send_alert("Notion Collector Failed", f"The Notion collector failed with error: {e}")
        logger.end_section()

    # Nothing executed check
    if executed_collectors == 0:
        logger.info("Nothing scheduled to run.")
        session.skipped = True
        session.finish()
        report.finish()
        report.write(day_dir)
        # Send backup report via email and Telegram
        try:
            from lib.reporting import send_backup_report
            log_file = logger.log_file
            send_backup_report(day_dir, log_file)
        except Exception as e:
            logger.error(f"Failed to send backup report: {e}")
        logger.finish()
        return

    # Manifest
    logger.section("Manifest")
    manifest = build_manifest(day_dir, backup_id=backup_id)
    logger.end_section()

    # Archive
    logger.section("Archive")
    artifact = build_archive(backup_id=backup_id, day=today, manifest=manifest)
    session.artifact = artifact
    report.archive(
        backup_id=artifact.backup_id,
        file=artifact.archive.name,
        sha256=artifact.sha256.name,
        size=artifact.size,
    )
    logger.end_section()

    # Verification
    logger.section("Verification")
    try:
        verify_archive(artifact)
        session.verified = True
        report.verification(verified=True)
    except Exception as e:
        report.error(str(e))
        report.verification(verified=False)
        raise
    logger.end_section()

    # Storage
    logger.section("Storage")
    repository = Repository()
    if not repository.upload(artifact):
        raise RuntimeError("Failed to store backup in repository.")
    # The archive was built in workspace/archive/ and then copied into the
    # repository. Nothing ever removed the staging copy, so every run left a
    # second full archive behind — 155 MB a day on top of the backups
    # themselves, and enough to fill this disk in about four months. It was
    # never noticed because cleanup_workspace only considers dated day
    # folders, and "archive" is not a date.
    #
    # Dropped only once the repository copy is confirmed present and the same
    # size: staging is the sole copy until then, and a cleanup that runs
    # before the thing it depends on is how backups get lost.
    try:
        stored = repository.root / "archives" / artifact.archive.name
        if stored.is_file() and stored.stat().st_size == artifact.archive.stat().st_size:
            freed = artifact.archive.stat().st_size
            artifact.archive.unlink()
            logger.info(f"Staging copy removed, {freed // 1048576} MB freed "
                        f"(the repository holds it now)")
        else:
            logger.warning("Staging copy kept — the repository copy does not "
                           "match it yet")
    except OSError as exc:
        logger.warning(f"Could not remove the staging copy: {exc}")

    metadata = MetadataStore(repository.root)
    result = SyncEngine().sync()
    session.replicated["repository"] = True
    session.replicated["usb"] = result["usb"]
    session.replicated["backblaze"] = result["backblaze"]
    uploaded = ["Repository"]
    if result["usb"].get("status"):
        uploaded.append("USB")
    if result["backblaze"].get("status"):
        uploaded.append("Backblaze")
    report.archive(uploaded_to=uploaded)

    # Record what was stored and where, then put that record in the bucket
    # beside the archives. Without it, "is the cloud copy complete?" has no
    # answer — a listing can only agree with itself.
    try:
        from storage import ledger
        from storage.config import BACKBLAZE_REMOTE

        ledger.record(repository.root, artifact, {
            "repository": True,
            "usb": bool(result["usb"].get("status")),
            "backblaze": bool(result["backblaze"].get("status")),
        })
        if result["backblaze"].get("status"):
            problem = ledger.publish(repository.root, BACKBLAZE_REMOTE)
            if problem:
                logger.warning(f"Could not publish the ledger: {problem}")
            else:
                logger.info("Ledger updated and published")
    except Exception as e:
        logger.warning(f"Could not update the ledger: {e}")

    logger.end_section()

    # Cleanup
    logger.section("Cleanup")
    deleted = cleanup_workspace()
    report.cleanup(removed=deleted)
    logger.end_section()

    # Retention (per-destination windows)
    logger.section("Retention")
    try:
        from .retention import apply_retention, misconfigured

        # A retention setting that is not a number silently kept everything
        # for ever. Say so, rather than letting the disk fill in silence.
        for key in ("LOCAL_RETENTION_DAYS", "BACKBLAZE_RETENTION_DAYS",
                    "USB_RETENTION_DAYS"):
            complaint = misconfigured(key)
            if complaint:
                logger.warning(complaint)

        for outcome in apply_retention():
            logger.info(outcome.summary)
            for skipped in outcome.skipped:
                logger.warning(
                    f"Kept {skipped} locally — no copy found on another destination"
                )
            if outcome.error:
                logger.error(f"{outcome.name}: {outcome.error}")
    except Exception as e:
        logger.error(f"Retention failed: {e}")
    logger.end_section()

    # Final report
    session.finish()
    report.finish()
    report.write(day_dir)
    metadata.append(artifact, session)
    ReportWriter().write_daily(artifact, session)
    # Send backup report via email and Telegram
    try:
        from lib.reporting import send_backup_report
        log_file = logger.log_file
        send_backup_report(day_dir, log_file)
    except Exception as e:
        logger.error(f"Failed to send backup report: {e}")

    # The report only exists now — the sync above ran before it was written,
    # so every report reached Backblaze and the office drive one run late,
    # and the newest run's report was never the one on the drive. That is
    # precisely the report someone checking on this morning's backup wants.
    # Nothing else has changed since the first sync, and rclone skips what it
    # already has, so this ships a few kilobytes of Markdown and stops.
    try:
        SyncEngine().sync()
        logger.info("Report synced to the offsite copies")
    except Exception as e:
        logger.warning(f"Could not sync the report: {e}")

    logger.finish()


if __name__ == "__main__":
    main()
