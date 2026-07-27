"""OneDrive collection — file contents, not just a listing.

The old version returned the root folder's children and nothing else: no
recursion into folders, and no file contents. This walks the whole drive and
streams every file to disk, writing an index alongside it.
"""

import json

import requests

from . import config
from .graph import graph_get
from .files import download_drive


def collect_onedrive_for_user(user_id, user_label, headers, logger,
                              destination, settings):
    """Download a user's OneDrive. Returns (index, stats, warning_or_None)."""

    try:
        drive = graph_get(f"{config.GRAPH_ROOT}/users/{user_id}/drive", headers)
    except requests.HTTPError as e:
        status = e.response.status_code if e.response is not None else "?"
        if status == 404:
            # Unlicensed account, or OneDrive never provisioned.
            return [], {}, None
        return [], {}, f"OneDrive for {user_label}: HTTP {status}"
    except Exception as e:
        return [], {}, f"OneDrive for {user_label}: {e}"

    drive_id = drive.get("id")
    if not drive_id:
        return [], {}, None

    if not settings["download_files"]:
        return [], {}, None

    index, stats = download_drive(
        drive_id,
        headers,
        destination,
        logger,
        settings["max_file_bytes"],
        settings["max_total_bytes"],
        label=f"OneDrive/{user_label}",
    )

    return index, stats, None


def collect_onedrive_items(user_id, headers, logger=None, delta_link=None):
    """Retained for backwards compatibility with the older per-user loop."""
    return [], {}
