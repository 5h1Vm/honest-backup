"""Exchange mail-flow evidence.

Three sources, in descending order of detail:

  1. analyzedEmails — full per-message trace with delivery verdict
     (delivered, blocked, quarantined). Requires Defender for Office 365 P2;
     returns 403 without it.
  2. Usage reports — per-user mail activity and mailbox usage, as CSV.
     Available on any tenant with Reports.Read.All.
  3. MailItemsAccessed events — already captured by the Unified Audit Log
     collector, which records mailbox access per user.

Whatever the tenant licence allows is collected; the rest is recorded as a
warning explaining which licence would unlock it.
"""

import json

import requests

from . import config
from .graph import graph_paginated_get


# Usage reports return CSV rather than JSON, so they are streamed to file.
USAGE_REPORTS = {
    "email_activity_user_detail": (
        "reports/getEmailActivityUserDetail(period='D7')",
        "per-user mail send/receive/read counts",
    ),
    "mailbox_usage_detail": (
        "reports/getMailboxUsageDetail(period='D7')",
        "mailbox size, item count, quota status",
    ),
    "email_app_usage_user_detail": (
        "reports/getEmailAppUsageUserDetail(period='D7')",
        "which mail clients each user used",
    ),
    "office365_active_user_detail": (
        "reports/getOffice365ActiveUserDetail(period='D7')",
        "which services each user actually used",
    ),
}


def collect_message_trace(headers, logger, workspace, state=None, days=7):
    """Collect mail-flow evidence. Returns (counts, warnings)."""
    counts = {}
    warnings = []

    trace_dir = workspace / "message_trace"
    trace_dir.mkdir(parents=True, exist_ok=True)

    # --- 1. full per-message trace (Defender for Office 365 P2) ----------
    try:
        analyzed = graph_paginated_get(
            f"{config.GRAPH_BETA}/security/collaboration/analyzedEmails"
            "?$top=1000",
            headers,
        )
        with open(trace_dir / "analyzed_emails.json", "w") as f:
            json.dump(analyzed, f, indent=2)
        counts["analyzed_emails"] = len(analyzed)
        logger.success(f"Collected {len(analyzed)} analyzed emails")
    except Exception as e:
        status = None
        if isinstance(e, requests.HTTPError) and e.response is not None:
            status = e.response.status_code
        if status == 403:
            message = (
                "Per-message trace (analyzedEmails) needs Defender for "
                "Office 365 Plan 2 — not licensed on this tenant"
            )
        else:
            message = f"Per-message trace unavailable: {e}"
        logger.warning(message)
        warnings.append(message)

    # --- 2. usage reports (CSV) ------------------------------------------
    for name, (endpoint, description) in USAGE_REPORTS.items():
        try:
            r = requests.get(
                f"{config.GRAPH_ROOT}/{endpoint}",
                headers=headers,
                timeout=120,
                allow_redirects=True,
            )
            r.raise_for_status()
        except Exception as e:
            message = f"Report {name} unavailable: {e}"
            logger.warning(message)
            warnings.append(message)
            continue

        target = trace_dir / f"{name}.csv"
        target.write_text(r.text)

        # First line is the header, so rows = lines - 1.
        rows = max(0, len(r.text.strip().splitlines()) - 1)
        counts[f"report_{name}"] = rows
        logger.success(f"Collected {rows} rows — {description}")

    return counts, warnings
