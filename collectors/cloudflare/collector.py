"""Cloudflare collection.

Rewritten to fix three structural problems in the previous version:

  1. No pagination — every dataset fetched page one and stopped, which capped
     the account audit log at 100 entries and would silently truncate any
     zone with more than 100 DNS records.
  2. One zone only — the collector read a single ZONE_ID, so every other zone
     on the account was invisible.
  3. No date window on audit logs — there was no way to accumulate history,
     so events aged out of our archive as soon as they left the first page.

Audit logs are now collected by date range with a checkpoint, so each run
picks up where the last one finished.
"""

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .config import ZONE_ID, ACCOUNT_ID
from . import config as cf_config
from .api import get, get_all


BASE = "https://api.cloudflare.com/client/v4"

STATE_FILE = (
    Path(__file__).resolve().parents[2] / "state" / "cloudflare" / "state.json"
)

# Cloudflare keeps audit logs for 18 months; on the first run we reach back a
# year so the archive starts with real history rather than only today.
FIRST_RUN_LOOKBACK_DAYS = 365


# name -> (category, path template, is_collection)
ZONE_DATASETS = {
    "zone":            ("zone", "/zones/{zone}", False),
    "dns_records":     ("zone", "/zones/{zone}/dns_records", True),
    "zone_settings":   ("zone", "/zones/{zone}/settings", True),
    "dnssec":          ("zone", "/zones/{zone}/dnssec", False),
    # Rulesets is where Cloudflare has consolidated WAF, rate limiting,
    # transforms and redirects. The legacy Page Rules and Rate Limits
    # endpoints are deprecated (410 Gone) and are covered by this.
    "rulesets":        ("zone", "/zones/{zone}/rulesets", True),
    "certificate_packs": ("zone", "/zones/{zone}/ssl/certificate_packs", True),
    "load_balancers":  ("zone", "/zones/{zone}/load_balancers", True),
    "firewall_rules":  ("security", "/zones/{zone}/firewall/rules", True),
    "filters":         ("security", "/zones/{zone}/filters", True),
    "waf_packages":    ("security", "/zones/{zone}/firewall/waf/packages", True),
}

ACCOUNT_DATASETS = {
    "access_applications":  ("zerotrust", "/accounts/{account}/access/apps", True),
    "access_groups":        ("zerotrust", "/accounts/{account}/access/groups", True),
    "access_users":         ("zerotrust", "/accounts/{account}/access/users", True),
    "service_tokens":       ("zerotrust", "/accounts/{account}/access/service_tokens", True),
    "identity_providers":   ("zerotrust", "/accounts/{account}/access/identity_providers", True),
    "device_posture":       ("zerotrust", "/accounts/{account}/devices/posture", True),
    "devices":              ("zerotrust", "/accounts/{account}/devices", True),
    "gateway_rules":        ("zerotrust", "/accounts/{account}/gateway/rules", True),
    "gateway_lists":        ("zerotrust", "/accounts/{account}/gateway/lists", True),
    "tunnels":              ("zerotrust", "/accounts/{account}/cfd_tunnel", True),
    "account_members":      ("account", "/accounts/{account}/members", True),
    "account_roles":        ("account", "/accounts/{account}/roles", True),
    "account_subscriptions": ("account", "/accounts/{account}/subscriptions", True),
    "workers_scripts":      ("account", "/accounts/{account}/workers/scripts", True),
}


def save_json(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w") as f:
        json.dump(data, f, indent=2)


def count(data):
    if isinstance(data, list):
        return len(data)
    if isinstance(data, dict):
        result = data.get("result")
        if isinstance(result, list):
            return len(result)
        if isinstance(result, dict):
            return 1
    return 0


def load_state():
    if STATE_FILE.exists():
        try:
            return json.loads(STATE_FILE.read_text())
        except Exception:
            return {}
    return {}


def save_state(state):
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    STATE_FILE.write_text(json.dumps(state, indent=2))


def list_zones(logger, stats):
    """Every zone on the account, falling back to the configured zone."""
    try:
        body = get_all(f"{BASE}/zones")
        zones = body.get("result", [])
        if zones:
            logger.info(f"Found {len(zones)} zones on the account")
            return zones
    except Exception as e:
        stats["warnings"].append(f"Zone enumeration failed: {e}")
        logger.warning(f"Could not list zones: {e}")

    if ZONE_ID:
        logger.info("Falling back to the single configured zone")
        return [{"id": ZONE_ID, "name": ZONE_ID}]
    return []


def collect_audit_logs(root, logger, stats, state):
    """Account audit logs, by date range, continuing from the last run."""
    audit_dir = root / "audit"
    audit_dir.mkdir(parents=True, exist_ok=True)

    now = datetime.now(timezone.utc)
    checkpoint = state.get("audit_logs_until")

    if checkpoint:
        try:
            since = datetime.fromisoformat(checkpoint)
        except ValueError:
            since = now - timedelta(days=FIRST_RUN_LOOKBACK_DAYS)
    else:
        since = now - timedelta(days=FIRST_RUN_LOOKBACK_DAYS)
        logger.info(
            f"First audit log run — reaching back {FIRST_RUN_LOOKBACK_DAYS} days"
        )

    try:
        body = get_all(
            f"{BASE}/accounts/{ACCOUNT_ID}/audit_logs",
            params={
                "since": since.strftime("%Y-%m-%dT%H:%M:%SZ"),
                "before": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
                "direction": "desc",
            },
            per_page=1000,
        )
    except Exception as e:
        message = f"Audit Logs failed: {e}"
        logger.warning(message)
        stats["warnings"].append(message)
        return

    entries = body.get("result", [])

    # Append to a rolling archive so history accumulates across runs instead of
    # being overwritten by whatever the current window returned.
    archive_path = audit_dir / "audit_logs.json"
    existing = []
    if archive_path.exists():
        try:
            previous = json.loads(archive_path.read_text())
            existing = (
                previous.get("result", [])
                if isinstance(previous, dict) else previous
            )
        except Exception:
            existing = []

    seen = {e.get("id") for e in existing if isinstance(e, dict)}
    added = [e for e in entries if e.get("id") not in seen]
    combined = existing + added

    save_json(archive_path, {
        "result": combined,
        "result_info": {
            "total": len(combined),
            "added_this_run": len(added),
            "window_since": since.isoformat(),
            "window_until": now.isoformat(),
        },
    })

    # Also keep this run's slice on its own for point-in-time evidence.
    save_json(audit_dir / f"audit_logs_{now.strftime('%Y-%m-%d')}.json", entries)

    state["audit_logs_until"] = now.isoformat()
    stats["items"]["audit_logs"] = len(entries)
    stats["items"]["audit_logs_archive_total"] = len(combined)
    logger.success(f"Collected {len(entries)} ({len(added)} new)")


def collect_access_logs(root, logger, stats):
    """Zero Trust authentication events — who reached which application."""
    try:
        body = get_all(
            f"{BASE}/accounts/{ACCOUNT_ID}/access/logs/access_requests",
            per_page=1000,
        )
    except Exception as e:
        message = (
            f"Access authentication logs unavailable: {e}"
        )
        logger.warning(message)
        stats["warnings"].append(message)
        return

    save_json(root / "audit" / "access_requests.json", body)
    total = count(body)
    stats["items"]["access_requests"] = total
    logger.success(f"Collected {total}")


def collect(workspace, logger):
    cf_config.WORKSPACE = Path(workspace)
    root = cf_config.WORKSPACE

    stats = {
        "status": "success",
        "items": {},
        "warnings": [],
        "errors": [],
    }

    state = load_state()

    for name in ("zone", "security", "zerotrust", "audit", "account"):
        (root / name).mkdir(parents=True, exist_ok=True)

    # ------------------------------------------------------------------
    # Zone-scoped datasets, for every zone on the account
    # ------------------------------------------------------------------
    zones = list_zones(logger, stats)
    stats["items"]["zones"] = len(zones)
    save_json(root / "zone" / "zones.json", zones)

    for zone in zones:
        zone_id = zone.get("id")
        zone_name = zone.get("name", zone_id)
        if not zone_id:
            continue

        logger.info(f"=== Zone {zone_name} ===")
        safe_zone = str(zone_name).replace("/", "_")

        for name, (category, template, is_collection) in ZONE_DATASETS.items():
            url = BASE + template.format(zone=zone_id)
            try:
                logger.info(name.replace("_", " ").title())
                data = get_all(url) if is_collection else get(url)
            except Exception as e:
                message = f"{zone_name}/{name} failed: {e}"
                logger.warning(message)
                stats["warnings"].append(message)
                continue

            # One folder per zone once more than one zone exists.
            if len(zones) > 1:
                target = root / category / safe_zone / f"{name}.json"
            else:
                target = root / category / f"{name}.json"

            save_json(target, data)
            total = count(data)
            stats["items"][f"{safe_zone}/{name}" if len(zones) > 1 else name] = total
            logger.success(f"Collected {total}")

    # ------------------------------------------------------------------
    # Account-scoped datasets
    # ------------------------------------------------------------------
    logger.info("=== Account ===")
    for name, (category, template, is_collection) in ACCOUNT_DATASETS.items():
        url = BASE + template.format(account=ACCOUNT_ID)
        try:
            logger.info(name.replace("_", " ").title())
            data = get_all(url) if is_collection else get(url)
        except Exception as e:
            message = f"{name} failed: {e}"
            logger.warning(message)
            stats["warnings"].append(message)
            continue

        save_json(root / category / f"{name}.json", data)
        total = count(data)
        stats["items"][name] = total
        logger.success(f"Collected {total}")

    # ------------------------------------------------------------------
    # Access policies, resolved per application
    # ------------------------------------------------------------------
    try:
        logger.info("Access Policies")
        apps = get_all(f"{BASE}/accounts/{ACCOUNT_ID}/access/apps")
        policies = []
        for app in apps.get("result", []):
            app_id = app.get("id")
            if not app_id:
                continue
            result = get_all(
                f"{BASE}/accounts/{ACCOUNT_ID}/access/apps/{app_id}/policies"
            )
            policies.append({
                "application": app.get("name"),
                "application_id": app_id,
                "policies": result.get("result", []),
            })
        save_json(root / "zerotrust" / "access_policies.json", policies)
        stats["items"]["access_policies"] = len(policies)
        logger.success(f"Collected {len(policies)}")
    except Exception as e:
        message = f"Access Policies failed: {e}"
        logger.warning(message)
        stats["warnings"].append(message)

    # ------------------------------------------------------------------
    # Logs
    # ------------------------------------------------------------------
    logger.info("=== Logs ===")
    collect_audit_logs(root, logger, stats, state)
    collect_access_logs(root, logger, stats)

    save_state(state)

    if stats["warnings"] and not stats["items"]:
        stats["status"] = "failed"
    elif stats["warnings"]:
        stats["status"] = "partial"

    logger.success("Cloudflare collection complete")
    return stats
