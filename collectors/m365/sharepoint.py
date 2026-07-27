"""SharePoint site inventory and document library contents.

Previously this only looked at sites a user *followed*, which misses every site
nobody happens to follow, and it recorded a file listing rather than the files
themselves. This enumerates all sites in the tenant and downloads each document
library.
"""

import json

from . import config
from .graph import graph_paginated_get
from .files import download_drive


def list_all_sites(headers, logger):
    """Every site in the tenant, trying each Graph shape in turn."""
    attempts = [
        (f"{config.GRAPH_ROOT}/sites?search=*", "search"),
        (f"{config.GRAPH_ROOT}/sites/getAllSites", "getAllSites"),
        (f"{config.GRAPH_ROOT}/sites?$select=id,name,displayName,webUrl", "list"),
    ]

    last_error = None
    for url, label in attempts:
        try:
            sites = graph_paginated_get(url, headers)
            if sites:
                logger.info(f"Found {len(sites)} sites via {label}")
                return sites, None
        except Exception as e:
            last_error = e
            continue

    return [], last_error


def collect_sharepoint(headers, logger, workspace, settings):
    """Collect all SharePoint sites and their document libraries.

    Returns (counts, warnings).
    """
    counts = {}
    warnings = []

    sp_dir = workspace / "sharepoint"
    sp_dir.mkdir(parents=True, exist_ok=True)

    sites, error = list_all_sites(headers, logger)

    if not sites:
        message = (
            "SharePoint unavailable — no sites could be listed"
            + (f" ({error})" if error else "")
        )
        logger.warning(message)
        return counts, [message]

    with open(sp_dir / "sites.json", "w") as f:
        json.dump(sites, f, indent=2)
    counts["sites"] = len(sites)

    if not settings["download_files"]:
        logger.info("File download disabled; recorded site inventory only")
        return counts, warnings

    total_files = 0
    total_bytes = 0

    for site in sites:
        site_id = site.get("id")
        site_name = site.get("name") or site.get("displayName") or site_id
        if not site_id:
            continue

        logger.info(f"[+] SharePoint site {site_name}")

        try:
            drives = graph_paginated_get(
                f"{config.GRAPH_ROOT}/sites/{site_id}/drives", headers
            )
        except Exception as e:
            warnings.append(f"site {site_name}: could not list libraries: {e}")
            continue

        for drive in drives:
            drive_id = drive.get("id")
            drive_name = drive.get("name", "documents")
            if not drive_id:
                continue

            safe_site = str(site_name).replace("/", "_")[:80]
            safe_drive = str(drive_name).replace("/", "_")[:80]
            destination = sp_dir / "content" / safe_site / safe_drive

            index, stats = download_drive(
                drive_id,
                headers,
                destination,
                logger,
                settings["max_file_bytes"],
                settings["max_total_bytes"],
                label=f"{site_name}/{drive_name}",
            )

            index_dir = sp_dir / "index" / safe_site
            index_dir.mkdir(parents=True, exist_ok=True)
            with open(index_dir / f"{safe_drive}.json", "w") as f:
                json.dump(index, f, indent=2)

            total_files += stats["downloaded"]
            total_bytes += stats["bytes"]
            logger.success(
                f"{stats['downloaded']} files "
                f"({stats['bytes'] / 1048576:.1f} MB)"
            )

    counts["files_downloaded"] = total_files
    counts["bytes_downloaded"] = total_bytes
    return counts, warnings


def collect_sharepoint_for_user(user_id, headers, logger=None, delta_link=None):
    """Retained so existing callers keep working. Tenant-wide collection is
    handled by collect_sharepoint(); per-user followed sites are redundant
    once every site is enumerated."""
    return [], {}
