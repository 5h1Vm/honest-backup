"""Security posture and Microsoft Defender collection.

Everything here comes through Microsoft Graph using the same app registration
as the rest of the collector — Defender does not need a separate API or a
separate credential. Graph exposes the unified Defender XDR surface:

  security/alerts_v2      Defender alerts (Endpoint, Office 365, Identity, Cloud Apps)
  security/incidents      alerts grouped into incidents
  security/runHuntingQuery  advanced hunting over raw Defender telemetry

Datasets that depend on a licence the tenant does not hold (for example
Identity Protection risky users, which needs Entra ID P2) are recorded as
warnings rather than failures, so one missing licence never fails the backup.
"""

import json

from . import config
from .graph import graph_paginated_get, graph_post


# name -> (endpoint, description)
SECURITY_ENDPOINTS = {
    "alerts": ("security/alerts_v2", "Defender alerts"),
    "incidents": ("security/incidents", "Defender incidents"),
    "secure_score": ("security/secureScores", "Secure Score history"),
    "secure_score_controls": (
        "security/secureScoreControlProfiles", "Secure Score control profiles"
    ),
    "risk_detections": (
        "identityProtection/riskDetections", "Identity Protection risk events"
    ),
    "risky_users": (
        "identityProtection/riskyUsers", "Identity Protection risky users"
    ),
}

# Advanced hunting queries. Tables vary by which Defender workloads the tenant
# has onboarded, so each is attempted independently.
HUNTING_QUERIES = {
    "hunt_alerts": "AlertInfo | where Timestamp > ago(24h) | limit 5000",
    "hunt_alert_evidence": (
        "AlertEvidence | where Timestamp > ago(24h) | limit 5000"
    ),
    "hunt_identity_logons": (
        "IdentityLogonEvents | where Timestamp > ago(24h) | limit 5000"
    ),
    "hunt_cloud_app_events": (
        "CloudAppEvents | where Timestamp > ago(24h) | limit 5000"
    ),
    "hunt_device_events": (
        "DeviceEvents | where Timestamp > ago(24h) | limit 5000"
    ),
    "hunt_device_logons": (
        "DeviceLogonEvents | where Timestamp > ago(24h) | limit 5000"
    ),
    "hunt_email_events": (
        "EmailEvents | where Timestamp > ago(24h) | limit 5000"
    ),
}


def collect_security(headers, logger, workspace):
    """Write one JSON file per security dataset. Returns (counts, warnings)."""
    counts = {}
    warnings = []

    security_dir = workspace / "security"
    security_dir.mkdir(parents=True, exist_ok=True)

    for name, (endpoint, description) in SECURITY_ENDPOINTS.items():
        logger.info(f"[+] Security {name}")

        try:
            data = graph_paginated_get(
                f"{config.GRAPH_ROOT}/{endpoint}", headers
            )
        except Exception as e:
            message = f"{description} unavailable: {e}"
            logger.warning(message)
            warnings.append(message)
            continue

        with open(security_dir / f"{name}.json", "w") as f:
            json.dump(data, f, indent=2)

        counts[name] = len(data)
        logger.success(f"Collected {len(data)}")

    # --- advanced hunting -------------------------------------------------
    hunting_dir = security_dir / "hunting"
    hunting_dir.mkdir(parents=True, exist_ok=True)

    for name, query in HUNTING_QUERIES.items():
        logger.info(f"[+] Hunting {name}")
        try:
            response = graph_post(
                f"{config.GRAPH_ROOT}/security/runHuntingQuery",
                headers,
                {"Query": query},
            )
        except Exception as e:
            # A missing table means that Defender workload is not onboarded.
            message = f"Hunting {name} unavailable: {e}"
            logger.warning(message)
            warnings.append(message)
            continue

        results = response.get("results", [])
        with open(hunting_dir / f"{name}.json", "w") as f:
            json.dump(results, f, indent=2)

        counts[name] = len(results)
        logger.success(f"Collected {len(results)}")

    return counts, warnings
