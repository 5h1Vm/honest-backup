from datetime import datetime


def new_backup_id() -> str:
    """
    Example:

    2026-06-30_15-25-17
    """

    return datetime.now().strftime(
        "%Y-%m-%d_%H-%M-%S"
    )
