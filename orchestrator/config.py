from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parent.parent

CONFIG_FILE = PROJECT_ROOT / "config" / "backup.conf"


def load_config():

    cfg = {}

    if CONFIG_FILE.exists():

        with open(CONFIG_FILE) as f:

            for line in f:

                line = line.strip()

                if (
                    not line
                    or line.startswith("#")
                    or "=" not in line
                ):
                    continue

                key, value = line.split("=", 1)

                cfg[key.strip()] = value.strip()

    return cfg


CFG = load_config()


#
# Project Paths
#

BACKUP_TARGET = Path(
    CFG.get(
        "BACKUP_TARGET",
        "backupvault",
    )
)

REPOSITORY_PATH = Path(
    CFG.get(
        "REPOSITORY_PATH",
        str(BACKUP_TARGET),
    )
)

WORKSPACE = Path(
    CFG.get(
        "WORKSPACE",
        "workspace",
    )
)

STATE_DIR = Path(
    CFG.get(
        "STATE_DIR",
        "state",
    )
)

LOG_DIR = Path(
    CFG.get(
        "LOG_DIR",
        "logs",
    )
)

COLLECTOR_DIR = Path(
    CFG.get(
        "COLLECTOR_DIR",
        "collectors",
    )
)

RESTORE_DIR = Path(
    CFG.get(
        "RESTORE_DIR",
        "restore",
    )
)


#
# Keys
#

ARCHIVE_PUBLIC_KEY = PROJECT_ROOT / CFG.get(
    "ARCHIVE_PUBLIC_KEY",
    "config/keys/archive.pub",
)


#
# Retention
#

WORKSPACE_RETENTION_DAYS = int(
    CFG.get(
        "WORKSPACE_RETENTION_DAYS",
        "7",
    )
)

ARCHIVE_RETENTION_DAYS = int(
    CFG.get(
        "ARCHIVE_RETENTION_DAYS",
        "30",
    )
)