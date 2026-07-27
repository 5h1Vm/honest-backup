from datetime import datetime
from datetime import timedelta

import shutil

from .config import (
    WORKSPACE,
    WORKSPACE_RETENTION_DAYS
)


def cleanup_workspace():

    cutoff = (
        datetime.now()
        - timedelta(days=WORKSPACE_RETENTION_DAYS)
    )

    deleted = []

    for item in WORKSPACE.iterdir():

        if not item.is_dir():
            continue

        try:
            day = datetime.strptime(
                item.name,
                '%Y-%m-%d'
            )
        except Exception:
            continue

        if day < cutoff:
            shutil.rmtree(item)
            deleted.append(item.name)

    return deleted
