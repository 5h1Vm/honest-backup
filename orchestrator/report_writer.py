from pathlib import Path

from .config import CFG

REPORT_ROOT = (
    Path(
        CFG.get(
            "REPOSITORY_PATH",
            "placeholder-backupvault",
        )
    )
    / "reports"
)


class ReportWriter:
    def __init__(self):
        self.daily = REPORT_ROOT / "daily"
        self.daily.mkdir(
            parents=True,
            exist_ok=True,
        )

    def write_daily(
        self,
        artifact,
        session,
    ):
        filename = self.daily / f"{session.started.strftime('%Y-%m-%d')}.md"
        new_file = not filename.exists()
        with open(
            filename,
            "a",
            encoding="utf-8",
        ) as f:
            if new_file:
                f.write(f"""# HonestBackup Daily Report

Date: {session.started.strftime('%Y-%m-%d')}

---

""")

            f.write(f"""
## Backup

Backup ID : {artifact.backup_id}

Started : {session.started}

Finished : {session.finished}

Duration : {session.duration:.2f} seconds

Archive : {artifact.archive_name}

Checksum : {artifact.checksum_name}

Archive Size : {artifact.size:,} bytes

Verified : {"YES" if session.verified else "NO"}

Repository : {"YES" if session.replicated.get("repository") else "NO"}

USB : {"YES" if session.replicated.get("usb") else "NO"}

Backblaze : {"YES" if session.replicated.get("backblaze") else "NO"}

Collectors

""")

            for collector in session.collectors:
                f.write(f"- {collector}\n")

            f.write("""

------------------------------------------------------------

""")
