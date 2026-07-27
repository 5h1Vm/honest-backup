"""Microsoft Purview Unified Audit Log, via Microsoft Graph.

This is the log that records what people actually did: file opened, mail sent,
permission granted, admin setting changed — across Exchange, SharePoint,
OneDrive, Teams and Entra.

There are two ways to reach it:

  1. Office 365 Management Activity API (manage.office.com) — the classic
     route. Needs ActivityFeed.Read granted on a separate API resource.
  2. Graph  security/auditLog/queries  — an asynchronous search job.
     Needs AuditLogsQuery.Read.All, which is an ordinary Graph permission.

We use route 2 because it works with the app registration we already have.
Route 1 is attempted only as a fallback.

The Graph search is asynchronous: submit a query, poll until it succeeds, then
page through the records. A seven-day search on a small tenant takes roughly
two minutes to complete, so the poll timeout is generous by default.
"""

import json
import time
from datetime import datetime, timedelta, timezone

import requests

from . import config
from .graph import get_token, graph_paginated_get


# The beta endpoint is the one that accepts query submission today; v1.0
# exposes the collection but rejects POST on many tenants.
QUERY_ROOTS = [
    f"{config.GRAPH_BETA}/security/auditLog/queries",
    f"{config.GRAPH_ROOT}/security/auditLog/queries",
]

STATE_KEY = "unified_audit_until"


def _submit(headers, start, end):
    """Submit the search job. Returns (query_id, root) or raises."""
    body = {
        "displayName": f"honestbackup-{end.strftime('%Y%m%d-%H%M%S')}",
        "filterStartDateTime": start.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "filterEndDateTime": end.strftime("%Y-%m-%dT%H:%M:%SZ"),
    }

    last_error = None
    for root in QUERY_ROOTS:
        try:
            r = requests.post(root, headers=headers, json=body, timeout=120)
            r.raise_for_status()
            return r.json()["id"], root
        except Exception as e:
            last_error = e
            continue

    raise last_error


def _wait(headers, root, query_id, logger, timeout_seconds, poll_seconds):
    """Poll the job until it finishes. Returns the final status."""
    waited = 0
    while waited < timeout_seconds:
        time.sleep(poll_seconds)
        waited += poll_seconds

        try:
            r = requests.get(f"{root}/{query_id}", headers=headers, timeout=90)
            r.raise_for_status()
            status = r.json().get("status")
        except Exception as e:
            logger.warning(f"Unified audit poll failed: {e}")
            continue

        if status in ("succeeded", "failed", "cancelled"):
            return status

        if waited % 60 == 0:
            logger.info(f"  still searching ({waited}s elapsed)")

    return "timedout"


def collect_unified_audit(logger, workspace, hours=24, state=None,
                          timeout_seconds=900, poll_seconds=10):
    """Fetch Unified Audit Log records. Returns (counts, warnings)."""
    counts = {}
    warnings = []
    state = state if state is not None else {}

    audit_dir = workspace / "unified_audit"
    audit_dir.mkdir(parents=True, exist_ok=True)

    try:
        token = get_token()
    except Exception as e:
        message = f"Unified Audit Log: could not get a token ({e})"
        logger.warning(message)
        return counts, [message]

    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }

    end = datetime.now(timezone.utc)

    # Continue from where the last run finished so nothing is missed between
    # runs, but never ask for more than the retention window holds.
    checkpoint = state.get(STATE_KEY)
    if checkpoint:
        try:
            start = datetime.fromisoformat(checkpoint)
            if start.tzinfo is None:
                start = start.replace(tzinfo=timezone.utc)
        except ValueError:
            start = end - timedelta(hours=hours)
    else:
        # First run: reach back as far as the feed allows.
        start = end - timedelta(days=7)
        logger.info("First unified audit run — searching the last 7 days")

    # Guard against a pathological window if the job has not run in months.
    max_window = timedelta(days=30)
    if end - start > max_window:
        start = end - max_window

    logger.info(
        f"Unified audit search {start.strftime('%Y-%m-%d %H:%M')} "
        f"-> {end.strftime('%Y-%m-%d %H:%M')} UTC"
    )

    try:
        query_id, root = _submit(headers, start, end)
    except Exception as e:
        detail = ""
        if isinstance(e, requests.HTTPError) and e.response is not None:
            detail = e.response.text[:200]
        message = (
            f"Unified Audit Log unavailable: {e} {detail} "
            "(needs AuditLogsQuery.Read.All)"
        )
        logger.warning(message)
        return counts, [message]

    logger.info(f"Search submitted ({query_id[:8]}…), waiting for results")

    status = _wait(headers, root, query_id, logger,
                   timeout_seconds, poll_seconds)

    if status != "succeeded":
        message = (
            f"Unified Audit Log search did not complete (status: {status}). "
            "Records for this window will be picked up on the next run."
        )
        logger.warning(message)
        return counts, [message]

    try:
        records = graph_paginated_get(
            f"{root}/{query_id}/records?$top=1000", headers
        )
    except Exception as e:
        message = f"Unified Audit Log records could not be read: {e}"
        logger.warning(message)
        return counts, [message]

    # Split by workload so the archive mirrors how people think about it.
    by_service = {}
    for record in records:
        service = (record.get("service") or "Other").replace(" ", "")
        by_service.setdefault(service, []).append(record)

    stamp = end.strftime("%Y-%m-%d")
    with open(audit_dir / f"unified_audit_{stamp}.json", "w") as f:
        json.dump(records, f, indent=2)

    for service, entries in by_service.items():
        with open(audit_dir / f"{service}.json", "w") as f:
            json.dump(entries, f, indent=2)
        counts[f"ual_{service}"] = len(entries)

    counts["unified_audit_total"] = len(records)
    state[STATE_KEY] = end.isoformat()

    summary = ", ".join(
        f"{service} {len(entries)}"
        for service, entries in sorted(by_service.items())
    )
    logger.success(f"Collected {len(records)} records ({summary})")

    return counts, warnings
