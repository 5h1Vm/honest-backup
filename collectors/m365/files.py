"""Actual file content download for OneDrive and SharePoint document libraries.

The previous collectors recorded only a listing of the root folder — enough to
know a file existed, not enough to get it back. This walks a drive recursively
and streams every file to disk alongside a metadata index.

Size and total budget are capped from backup.conf so a single large library
cannot blow up a backup run.
"""

import json

import requests

from . import config
from .graph import graph_paginated_get, graph_download


def _safe_relative(path_parts, name):
    """Build a filesystem-safe relative path from Graph path segments."""
    cleaned = []
    for part in list(path_parts) + [name]:
        part = str(part).replace("\\", "_").replace("/", "_").strip()
        # Keep names sane on every filesystem.
        for bad in ('"', ":", "*", "?", "<", ">", "|", "\0"):
            part = part.replace(bad, "_")
        part = part.strip(". ") or "_"
        cleaned.append(part[:120])
    return cleaned


def download_drive(
    drive_id,
    headers,
    destination,
    logger,
    max_file_bytes,
    max_total_bytes,
    label="drive",
):
    """Recursively download a drive's contents.

    Returns (index, stats) where index is a list of file records and stats
    holds counts and byte totals.
    """
    index = []
    stats = {
        "files": 0,
        "folders": 0,
        "downloaded": 0,
        "skipped_too_large": 0,
        "skipped_budget": 0,
        "failed": 0,
        "bytes": 0,
    }

    def walk(item_id, path_parts):
        if stats["bytes"] >= max_total_bytes:
            return

        url = (
            f"{config.GRAPH_ROOT}/drives/{drive_id}/items/{item_id}/children"
            "?$select=id,name,size,file,folder,lastModifiedDateTime,"
            "createdDateTime,webUrl,parentReference"
        )
        try:
            children = graph_paginated_get(url, headers)
        except Exception as e:
            logger.warning(f"{label}: could not list folder {'/'.join(path_parts)}: {e}")
            stats["failed"] += 1
            return

        for child in children:
            name = child.get("name", "unnamed")

            if "folder" in child:
                stats["folders"] += 1
                walk(child["id"], list(path_parts) + [name])
                continue

            if "file" not in child:
                continue

            stats["files"] += 1
            size = int(child.get("size") or 0)
            relative = _safe_relative(path_parts, name)

            record = {
                "id": child.get("id"),
                "name": name,
                "path": "/".join(relative),
                "size": size,
                "mimeType": child.get("file", {}).get("mimeType"),
                "lastModifiedDateTime": child.get("lastModifiedDateTime"),
                "createdDateTime": child.get("createdDateTime"),
                "webUrl": child.get("webUrl"),
                "sha256": child.get("file", {})
                              .get("hashes", {})
                              .get("sha256Hash"),
                "downloaded": False,
            }

            if size > max_file_bytes:
                record["skipReason"] = "larger than per-file limit"
                stats["skipped_too_large"] += 1
                index.append(record)
                continue

            if stats["bytes"] + size > max_total_bytes:
                record["skipReason"] = "run byte budget reached"
                stats["skipped_budget"] += 1
                index.append(record)
                continue

            target = destination.joinpath(*relative)
            try:
                written = graph_download(
                    f"{config.GRAPH_ROOT}/drives/{drive_id}/items/"
                    f"{child['id']}/content",
                    headers,
                    target,
                )
                record["downloaded"] = True
                stats["downloaded"] += 1
                stats["bytes"] += written
            except Exception as e:
                record["skipReason"] = f"download failed: {e}"
                stats["failed"] += 1

            index.append(record)

    walk("root", [])
    return index, stats


def download_message_attachments(
    user_id, messages, headers, destination, logger, max_file_bytes
):
    """Download attachments for messages that have them. Returns stats."""
    stats = {"messages_with_attachments": 0, "downloaded": 0,
             "skipped_too_large": 0, "failed": 0, "bytes": 0}

    for message in messages:
        if not message.get("hasAttachments"):
            continue
        message_id = message.get("id")
        if not message_id:
            continue

        stats["messages_with_attachments"] += 1

        try:
            attachments = graph_paginated_get(
                f"{config.GRAPH_ROOT}/users/{user_id}/messages/"
                f"{message_id}/attachments",
                headers,
            )
        except Exception as e:
            logger.warning(f"attachments for message {message_id}: {e}")
            stats["failed"] += 1
            continue

        for attachment in attachments:
            name = attachment.get("name", "attachment")
            size = int(attachment.get("size") or 0)

            if size > max_file_bytes:
                stats["skipped_too_large"] += 1
                continue

            content = attachment.get("contentBytes")
            if not content:
                # Item attachments (a forwarded mail, an event) have no
                # contentBytes; keep the metadata rather than the payload.
                continue

            import base64
            safe = _safe_relative([message_id], name)
            target = destination.joinpath(*safe)
            try:
                target.parent.mkdir(parents=True, exist_ok=True)
                data = base64.b64decode(content)
                target.write_bytes(data)
                stats["downloaded"] += 1
                stats["bytes"] += len(data)
            except Exception as e:
                logger.warning(f"attachment {name}: {e}")
                stats["failed"] += 1

    return stats
