"""Microsoft 365 collection.

Order of work:
  1. tenant snapshots   — who/what existed and how it was configured
  2. security posture   — Defender alerts, incidents, hunting, Secure Score
  3. identity posture   — MFA registration, devices, permission grants
  4. Entra logs         — sign-ins, directory audits, provisioning
  5. Unified Audit Log  — Purview activity across all workloads
  6. SharePoint         — every site, with document library contents
  7. Teams              — teams, channels, messages
  8. per-user mailbox   — mail, calendar, contacts, OneDrive contents
"""

from pathlib import Path
import json

from . import config
from .graph import get_token
from .state import (
    load_state, save_state, get_user_resource_state, set_user_resource_state
)
from .mail import collect_messages_for_user
from .calendar import collect_calendar_events
from .contacts import collect_contacts
from .audit import collect_audit
from .snapshots import collect_snapshots
from .security import collect_security
from .identity import collect_identity
from .unified_audit import collect_unified_audit
from .message_trace import collect_message_trace
from .sharepoint import collect_sharepoint
from .sharepoint_rest import collect_sharepoint_rest
from .teams import collect_teams
from .onedrive import collect_onedrive_for_user
from .files import download_message_attachments


def _settings():
    """Collection limits, read from config/backup.conf."""
    from lib.secrets import get_config
    cfg = get_config()

    def flag(key, default):
        return cfg.get(key, default).strip().lower() == "true"

    def number(key, default):
        try:
            return int(cfg.get(key, default))
        except (TypeError, ValueError):
            return int(default)

    return {
        "download_files": flag("M365_DOWNLOAD_FILES", "true"),
        "sharepoint_sites": cfg.get("SHAREPOINT_SITES", ""),
        "incremental": flag("INCREMENTAL", "true"),
        "download_attachments": flag("M365_DOWNLOAD_ATTACHMENTS", "true"),
        "download_messages": flag("M365_DOWNLOAD_TEAMS_MESSAGES", "true"),
        "unified_audit_hours": number("M365_UNIFIED_AUDIT_HOURS", "24"),
        "unified_audit_timeout": number("M365_UNIFIED_AUDIT_TIMEOUT_SEC", "900"),
        "max_file_bytes": number("M365_MAX_FILE_MB", "100") * 1024 * 1024,
        "max_total_bytes": number("M365_MAX_TOTAL_GB", "5") * 1024 * 1024 * 1024,
    }


def collect(workspace, logger):
    workspace = Path(workspace)
    workspace.mkdir(parents=True, exist_ok=True)
    config.WORKSPACE = workspace

    settings = _settings()

    stats = {
        "status": "success",
        "items": {},
        "warnings": [],
        "errors": [],
    }

    def log(level, msg):
        if logger:
            getattr(logger, level)(msg)
        else:
            print(f"[{level.upper()}] {msg}")

    def run_stage(name, fn):
        """Run one collection stage; a failure never aborts the others."""
        log("info", f"=== {name} ===")
        try:
            counts, warnings = fn()
            for key, value in (counts or {}).items():
                stats["items"][key] = value
            for warning in warnings or []:
                stats["warnings"].append(warning)
        except Exception as e:
            message = f"{name} failed: {e}"
            log("error", message)
            stats["errors"].append(message)

    try:
        token = get_token()
        headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        }
        log("info", "Token acquired")

        state = load_state()
        state.setdefault("users", {})
        state.setdefault("audit", {})

        # ------------------------------------------------------------------
        # 1. Tenant snapshots
        # ------------------------------------------------------------------
        run_stage(
            "Directory snapshots",
            lambda: collect_snapshots(headers, logger, workspace),
        )

        # ------------------------------------------------------------------
        # 2. Security posture and Defender
        # ------------------------------------------------------------------
        run_stage(
            "Security and Defender",
            lambda: collect_security(headers, logger, workspace),
        )

        # ------------------------------------------------------------------
        # 3. Identity and device posture
        # ------------------------------------------------------------------
        run_stage(
            "Identity posture",
            lambda: collect_identity(headers, logger, workspace),
        )

        # ------------------------------------------------------------------
        # 4. Entra logs (incremental)
        # ------------------------------------------------------------------
        log("info", "=== Entra logs ===")
        try:
            audit_data, audit_state_update = collect_audit(
                headers, logger, workspace
            )
            total = 0
            for dataset, data in (audit_data or {}).items():
                stats["items"][f"audit_{dataset}"] = len(data)
                total += len(data)
            log("info", f"Collected {total} Entra log entries")
            for key, value in (audit_state_update or {}).items():
                if value is not None:
                    state["audit"][key] = value
        except Exception as e:
            message = f"Entra logs failed: {e}"
            log("error", message)
            stats["errors"].append(message)

        # ------------------------------------------------------------------
        # 5. Unified Audit Log (Purview)
        # ------------------------------------------------------------------
        run_stage(
            "Unified Audit Log",
            lambda: collect_unified_audit(
                logger, workspace, settings["unified_audit_hours"],
                state=state,
                timeout_seconds=settings["unified_audit_timeout"],
            ),
        )

        run_stage(
            "Exchange message trace",
            lambda: collect_message_trace(headers, logger, workspace, state),
        )

        # ------------------------------------------------------------------
        # 6. SharePoint
        # ------------------------------------------------------------------
        # Graph first; if it cannot list a single site (this tenant returns
        # HTTP 503 on every /sites endpoint), fall back to SharePoint's own
        # REST API, which needs certificate auth.
        def sharepoint_stage():
            counts, warnings = collect_sharepoint(
                headers, logger, workspace, settings
            )
            if counts.get("sites"):
                return counts, warnings

            logger.info("Graph could not reach SharePoint — trying REST")
            rest_counts, rest_warnings = collect_sharepoint_rest(
                logger, workspace, settings
            )
            counts.update(rest_counts)
            return counts, warnings + rest_warnings

        run_stage("SharePoint", sharepoint_stage)

        # ------------------------------------------------------------------
        # 7. Teams
        # ------------------------------------------------------------------
        run_stage(
            "Teams",
            lambda: collect_teams(headers, logger, workspace, settings),
        )

        # ------------------------------------------------------------------
        # 8. Per-user mailbox content
        # ------------------------------------------------------------------
        log("info", "=== Per-user content ===")
        from .users import get_all_users
        users = get_all_users()
        log("info", f"Found {len(users)} users in the tenant")

        # Save the roster so a GUID directory can still be identified after
        # the account is deleted.
        users_dir = workspace / "users"
        users_dir.mkdir(parents=True, exist_ok=True)
        with open(users_dir / "_users.json", "w") as f:
            json.dump(users, f, indent=2)
        stats["items"]["users"] = len(users)

        mailbox_resources = [
            ("mail", collect_messages_for_user, "mail messages"),
            ("calendar", collect_calendar_events, "calendar events"),
            ("contacts", collect_contacts, "contacts"),
        ]

        for user in users:
            user_id = user.get("id")
            user_label = user.get("userPrincipalName", "unknown")
            if not user_id:
                log("warning", f"Skipping user with missing id: {user}")
                continue

            log("info", f"Processing user: {user_label}")
            user_dir = users_dir / user_id
            user_dir.mkdir(parents=True, exist_ok=True)

            for resource_name, collector_func, description in mailbox_resources:
                try:
                    current_state = get_user_resource_state(
                        state, user_id, resource_name
                    )
                    delta_link = None
                    if current_state and isinstance(current_state, dict):
                        delta_link = current_state.get("deltaLink")

                    data, state_update = collector_func(
                        user_id, headers, logger, delta_link=delta_link
                    )

                    if data:
                        with open(user_dir / f"{resource_name}.json", "w") as f:
                            json.dump(data, f, indent=2)
                        log("info",
                            f"Saved {len(data)} {description} for {user_label}")

                    # Mail attachments are separate objects in Graph.
                    if (resource_name == "mail" and data
                            and settings["download_attachments"]):
                        attachment_stats = download_message_attachments(
                            user_id, data, headers,
                            user_dir / "attachments", logger,
                            settings["max_file_bytes"],
                        )
                        if attachment_stats["downloaded"]:
                            log("info",
                                f"Saved {attachment_stats['downloaded']} "
                                f"attachments for {user_label}")
                            stats["items"][f"attachments_{user_id}"] = \
                                attachment_stats["downloaded"]

                    if state_update:
                        set_user_resource_state(
                            state, user_id, resource_name, state_update
                        )

                    stats["items"][f"{resource_name}_{user_id}"] = len(data)

                except Exception as e:
                    message = (
                        f"{resource_name} for {user_label} failed: {e}"
                    )
                    log("error", message)
                    stats["errors"].append(message)

            # OneDrive contents
            try:
                index, drive_stats, warning = collect_onedrive_for_user(
                    user_id, user_label, headers, logger,
                    user_dir / "onedrive", settings,
                )
                if warning:
                    stats["warnings"].append(warning)
                if index:
                    with open(user_dir / "onedrive_index.json", "w") as f:
                        json.dump(index, f, indent=2)
                    stats["items"][f"onedrive_{user_id}"] = \
                        drive_stats.get("downloaded", 0)
                    log("info",
                        f"OneDrive for {user_label}: "
                        f"{drive_stats.get('downloaded', 0)} files")
            except Exception as e:
                message = f"OneDrive for {user_label} failed: {e}"
                log("error", message)
                stats["errors"].append(message)

        save_state(state)
        log("info", "State saved")

        log("info", "M365 collection complete")
        stats["status"] = "success" if not stats["errors"] else "partial"

    except Exception as e:
        stats["status"] = "failed"
        stats["errors"].append(str(e))
        log("error", f"M365 collection failed: {e}")
        raise

    return stats
