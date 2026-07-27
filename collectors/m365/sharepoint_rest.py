"""SharePoint collection via the SharePoint REST API.

A fallback for when the Graph Sites API is unavailable — which is the case on
this tenant, where every `/sites` endpoint returns HTTP 503.

SharePoint's own REST API is a separate service and works when Graph does not,
but it will not accept an app-only token obtained with a client secret. It
answers "Unsupported app only token". App-only access to SharePoint REST
requires **certificate** authentication, which is why config/keys holds a
certificate for this purpose alone.

Setup (one time):
  1. Upload config/keys/sharepoint-app.cer to the app registration
     (Certificates & secrets -> Certificates -> Upload certificate)
  2. Ensure the app has the SharePoint application permission Sites.Read.All
     (this is separate from the Graph permission of the same name)
  3. Grant admin consent

Until that is done, this collector reports a clear warning and does nothing.
"""

import json

import requests

from .graph import get_token, certificate_available
from .files import _safe_relative


def _tenant_hosts(logger):
    """Work out the SharePoint hostnames for this tenant."""
    from .secrets import load_env

    env = load_env()
    # The tenant name comes from the verified domain, e.g. example.com
    # -> example.sharepoint.com and example-my.sharepoint.com
    from . import config
    from .graph import graph_paginated_get

    try:
        headers = {"Authorization": f"Bearer {get_token()}"}
        domains = graph_paginated_get(f"{config.GRAPH_ROOT}/domains", headers)
    except Exception:
        domains = []

    names = []
    for domain in domains:
        name = domain.get("id", "")
        if name.endswith(".onmicrosoft.com"):
            names.append(name.split(".")[0])
    for domain in domains:
        name = domain.get("id", "")
        if name and not name.endswith(".onmicrosoft.com"):
            names.append(name.split(".")[0])

    seen = []
    for name in names:
        if name and name not in seen:
            seen.append(name)
    return seen


def _rest_get(url, token, timeout=120):
    r = requests.get(
        url,
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/json;odata=nometadata",
        },
        timeout=timeout,
    )
    r.raise_for_status()
    return r.json()


def _download(url, token, destination, timeout=300):
    destination.parent.mkdir(parents=True, exist_ok=True)
    with requests.get(
        url,
        headers={"Authorization": f"Bearer {token}"},
        stream=True,
        timeout=timeout,
    ) as r:
        r.raise_for_status()
        written = 0
        with open(destination, "wb") as f:
            for chunk in r.iter_content(chunk_size=1024 * 256):
                if chunk:
                    f.write(chunk)
                    written += len(chunk)
    return written


def _walk_folder(host, site_path, folder_url, token, destination, logger,
                 settings, index, stats, depth=0):
    """Recursively download one folder's files."""
    if depth > 20 or stats["bytes"] >= settings["max_total_bytes"]:
        return

    base = f"https://{host}{site_path}/_api/web"
    quoted = folder_url.replace("'", "''")

    try:
        files = _rest_get(
            f"{base}/GetFolderByServerRelativeUrl('{quoted}')/Files", token
        ).get("value", [])
    except Exception as e:
        logger.warning(f"  could not list files in {folder_url}: {e}")
        stats["failed"] += 1
        files = []

    for item in files:
        name = item.get("Name", "unnamed")
        size = int(item.get("Length") or 0)
        relative = _safe_relative(
            folder_url.strip("/").split("/")[2:], name
        )

        record = {
            "name": name,
            "path": "/".join(relative),
            "size": size,
            "timeCreated": item.get("TimeCreated"),
            "timeLastModified": item.get("TimeLastModified"),
            "serverRelativeUrl": item.get("ServerRelativeUrl"),
            "downloaded": False,
        }

        stats["files"] += 1

        if size > settings["max_file_bytes"]:
            record["skipReason"] = "larger than per-file limit"
            stats["skipped_too_large"] += 1
            index.append(record)
            continue

        if stats["bytes"] + size > settings["max_total_bytes"]:
            record["skipReason"] = "run byte budget reached"
            stats["skipped_budget"] += 1
            index.append(record)
            continue

        target = destination.joinpath(*relative)

        # Incremental: in a hardlinked workspace yesterday's copy is already
        # here. If it is byte-for-byte the same size, the file has not changed
        # and there is nothing to fetch again.
        if settings.get("incremental", True) and target.exists():
            try:
                if target.stat().st_size == size and size > 0:
                    record["downloaded"] = True
                    record["unchanged"] = True
                    stats["unchanged"] += 1
                    index.append(record)
                    continue
            except OSError:
                pass

        server_relative = item.get("ServerRelativeUrl", "")
        try:
            written = _download(
                f"{base}/GetFileByServerRelativeUrl"
                f"('{server_relative}')/$value",
                token,
                target,
            )
            record["downloaded"] = True
            stats["downloaded"] += 1
            stats["bytes"] += written
        except Exception as e:
            record["skipReason"] = f"download failed: {e}"
            stats["failed"] += 1

        index.append(record)

    try:
        folders = _rest_get(
            f"{base}/GetFolderByServerRelativeUrl('{quoted}')/Folders", token
        ).get("value", [])
    except Exception:
        folders = []

    for folder in folders:
        name = folder.get("Name", "")
        if name in ("Forms", "_catalogs", "_private", "_vti_pvt"):
            continue
        child = folder.get("ServerRelativeUrl")
        if child:
            stats["folders"] += 1
            _walk_folder(host, site_path, child, token, destination, logger,
                         settings, index, stats, depth + 1)


def collect_sharepoint_rest(logger, workspace, settings):
    """Collect SharePoint document libraries over the REST API.

    Returns (counts, warnings).
    """
    counts = {}
    warnings = []

    if not certificate_available():
        message = (
            "SharePoint REST fallback not configured — upload "
            "config/keys/sharepoint-app.cer to the app registration and grant "
            "the SharePoint Sites.Read.All application permission"
        )
        logger.info(message)
        return counts, [message]

    tenants = _tenant_hosts(logger)
    if not tenants:
        return counts, ["SharePoint REST: could not determine the tenant name"]

    tenant = tenants[0]
    host = f"{tenant}.sharepoint.com"

    try:
        token = get_token(
            f"https://{host}/.default", use_certificate=True
        )
    except Exception as e:
        message = f"SharePoint REST token failed: {e}"
        logger.warning(message)
        return counts, [message]

    sp_dir = workspace / "sharepoint"
    sp_dir.mkdir(parents=True, exist_ok=True)

    # Find the sites. The search endpoint needs no Graph.
    # Fall back to the root site if search is unavailable. Extra sites can be
    # named in SHAREPOINT_SITES (comma-separated server-relative paths, e.g.
    # "/sites/Governance,/sites/Legal") for tenants where search is disabled.
    site_paths = [""] + [
        path.strip()
        for path in str(settings.get("sharepoint_sites", "")).split(",")
        if path.strip()
    ]
    try:
        webs = _rest_get(
            f"https://{host}/_api/search/query"
            "?querytext='contentclass:STS_Site'&rowlimit=500",
            token,
        )
        rows = (
            webs.get("PrimaryQueryResult", {})
                .get("RelevantResults", {})
                .get("Table", {})
                .get("Rows", [])
        )
        found = []
        for row in rows:
            cells = {c.get("Key"): c.get("Value") for c in row.get("Cells", [])}
            path = cells.get("Path", "")
            if path.startswith(f"https://{host}"):
                found.append(path[len(f"https://{host}"):] or "")
        if found:
            site_paths = sorted(set(found))
            logger.info(f"Found {len(site_paths)} sites via SharePoint search")
    except Exception as e:
        warnings.append(f"SharePoint site search failed, using defaults: {e}")

    total_files = 0
    total_unchanged = 0
    total_bytes = 0
    inventory = []

    for site_path in site_paths:
        label = site_path or "/"
        logger.info(f"[+] SharePoint site {label}")

        try:
            # RootFolder must be expanded, otherwise every library falls back
            # to the same default path and the same files are fetched again
            # once per library.
            lists = _rest_get(
                f"https://{host}{site_path}/_api/web/lists"
                "?$filter=BaseTemplate eq 101 and Hidden eq false"
                "&$expand=RootFolder"
                "&$select=Title,ItemCount,RootFolder/ServerRelativeUrl",
                token,
            ).get("value", [])
        except Exception as e:
            warnings.append(f"site {label}: could not list libraries: {e}")
            continue

        inventory.append({"site": label, "libraries": lists})

        if not settings["download_files"]:
            continue

        seen_folders = set()

        for library in lists:
            title = library.get("Title", "Documents")
            root_folder = (
                library.get("RootFolder") or {}
            ).get("ServerRelativeUrl")

            if not root_folder:
                warnings.append(
                    f"{label}/{title}: no root folder reported, skipped"
                )
                continue

            # Guard against two libraries reporting the same folder.
            if root_folder in seen_folders:
                continue
            seen_folders.add(root_folder)

            if not library.get("ItemCount"):
                logger.info(f"  {title}: empty, skipped")
                continue

            safe_site = (site_path.strip("/") or "root").replace("/", "_")[:80]
            safe_lib = str(title).replace("/", "_")[:80]
            destination = sp_dir / "content" / safe_site / safe_lib

            index = []
            stats = {
                "files": 0, "folders": 0, "downloaded": 0, "unchanged": 0,
                "skipped_too_large": 0, "skipped_budget": 0,
                "failed": 0, "bytes": 0,
            }

            _walk_folder(host, site_path, root_folder, token, destination,
                         logger, settings, index, stats)

            index_dir = sp_dir / "index" / safe_site
            index_dir.mkdir(parents=True, exist_ok=True)
            with open(index_dir / f"{safe_lib}.json", "w") as f:
                json.dump(index, f, indent=2)

            total_files += stats["downloaded"]
            total_unchanged += stats["unchanged"]
            total_bytes += stats["bytes"]
            fresh = stats["downloaded"]
            same = stats["unchanged"]
            logger.success(
                f"{title}: {fresh + same} files "
                f"({fresh} new or changed, {same} unchanged, "
                f"{stats['bytes'] / 1048576:.1f} MB fetched)"
            )

    with open(sp_dir / "sites_rest.json", "w") as f:
        json.dump(inventory, f, indent=2)

    counts["sharepoint_rest_sites"] = len(inventory)
    counts["sharepoint_rest_files"] = total_files
    counts["sharepoint_rest_unchanged"] = total_unchanged
    counts["sharepoint_rest_bytes"] = total_bytes
    return counts, warnings
