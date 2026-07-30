from datetime import datetime


def new_backup_id() -> str:
    """
    Example:

    2026-06-30_15-25-17

    In CRON_TIMEZONE, same as the log lines inside the backup it names —
    on a server whose clock runs in UTC, a naive datetime.now() here would
    stamp the folder five and a half hours behind every line printed into
    it during the run.
    """
    from lib.logger import display_zone

    return datetime.now(display_zone()).strftime("%Y-%m-%d_%H-%M-%S")
