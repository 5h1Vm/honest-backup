from orchestrator.config import CFG

BROWSER_EXECUTABLE = CFG.get(
    "NOTION_BROWSER_EXECUTABLE",
    "/usr/bin/microsoft-edge",
)

PROFILE_DIR = CFG.get(
    "NOTION_PROFILE_DIR",
    "state/notion/profile"
)

PROFILE_NAME = CFG.get(
    "NOTION_PROFILE_NAME",
    "Default"
)

WORKSPACE_URL = CFG.get(
    "NOTION_WORKSPACE_URL",
    ""
)

POLL_INTERVAL_SECONDS = int(
    CFG.get(
        "NOTION_POLL_INTERVAL_SECONDS",
        "60"
    )
)

EXPORT_TIMEOUT_MINUTES = int(
    CFG.get(
        "NOTION_EXPORT_TIMEOUT_MINUTES",
        "240"
    )
)