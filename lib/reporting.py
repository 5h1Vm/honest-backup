import json
import os
from pathlib import Path
from .alert import send_email_report, send_telegram_alert, send_telegram_document
from .secrets import get_config


COLLECTOR_LABELS = {
    "m365": "Microsoft 365",
    "cloudflare": "Cloudflare",
    "notion": "Notion",
}

STATUS_MARK = {
    "success": "OK",
    "partial": "PARTIAL",
    "failed": "FAILED",
    "disabled": "off",
}


def human_size(num_bytes) -> str:
    size = float(num_bytes or 0)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if size < 1024 or unit == "TB":
            return f"{int(size)} B" if unit == "B" else f"{size:.1f} {unit}"
        size /= 1024
    return f"{size:.1f} TB"


def collector_summary(report_data: dict, config: dict) -> list:
    """One line per collector: status, what was collected, what broke.

    Returns a list of dicts so both the email body and the Telegram message
    can be built from the same facts.
    """
    summary = []

    for name in ("m365", "cloudflare", "notion"):
        enabled = config.get(
            f'ENABLE_{name.upper()}', 'false'
        ).lower() == 'true'
        collectors = report_data.get('collectors', {})

        entry = {
            "name": name,
            "label": COLLECTOR_LABELS.get(name, name),
            "status": "disabled",
            "records": 0,
            "datasets": 0,
            "warnings": [],
            "errors": [],
            "stages": {},
        }

        if not enabled:
            summary.append(entry)
            continue

        if name not in collectors:
            entry["status"] = "skipped"
            entry["detail"] = "interval not reached"
            summary.append(entry)
            continue

        info = collectors[name]
        entry["status"] = info.get("status", "unknown")

        items = info.get("items") or {}
        if isinstance(items, dict):
            entry["datasets"] = len(items)
            # Byte totals are measurements, not records — counting them would
            # report "79,722,811 records" for an 80 MB download.
            entry["records"] = sum(
                value for key, value in items.items()
                if isinstance(value, int) and not key.endswith("_bytes")
            )
            entry["items"] = items

        entry["warnings"] = list(info.get("warnings") or [])
        entry["errors"] = list(info.get("errors") or [])
        entry["stages"] = dict(info.get("stages") or {})

        summary.append(entry)

    return summary


# Conditions that are not faults: a licence the tenant does not hold, an
# account with no mailbox, a dataset that is simply empty. These are facts
# about the tenant, not problems with the backup, so they are reported as
# "not collected, because…" rather than counted as failures.
EXPECTED_LIMITS = (
    "not licensed",
    "needs defender",
    "needs entra",
    "requires",
    "aadpremiumlicenserequired",
    "no mailbox",
    "no calendar",
    "no contacts",
    "has no onedrive",
    "404",
    "403 client error",
    "failed to resolve table",
    "unavailable: 400",
    "not supported",
    "does not support account owned tokens",
    "account owned tokens",
    "page rule",
    "rate_limits",
    "deprecated",
    "410",
    "no sites could be listed",
    "empty",
)


def is_expected_limit(message: str) -> bool:
    text = str(message).lower()
    return any(marker in text for marker in EXPECTED_LIMITS)


# Turn API errors into something a person can read. Order matters — the first
# match wins.
PLAIN_LANGUAGE = [
    ("risky users", "Risky user scoring — needs an Entra ID P2 licence"),
    ("analyzedemails", "Per-message mail tracing — needs Defender for Office 365 P2"),
    ("hunt_device_events", "Device event hunting — no devices onboarded to Defender"),
    ("hunt_device_logons", "Device sign-in hunting — no devices onboarded to Defender"),
    ("hunt_email_events", "Email hunting — needs Defender for Office 365 P2"),
    ("no sites could be listed", "SharePoint via Microsoft Graph — Microsoft outage (collected via the fallback instead)"),
    ("onedrive for", "Personal OneDrive — Microsoft Graph outage (HTTP 503)"),
    ("has no mailbox", "Mailbox — this account has no Exchange licence"),
    ("no calendar", "Calendar — this account has no Exchange licence"),
    ("no contacts", "Contacts — this account has no Exchange licence"),
    ("has no onedrive", "OneDrive — not provisioned for this account"),
    ("page rules", "Cloudflare Page Rules — replaced by Rulesets, which we collect"),
    ("rate_limits", "Cloudflare Rate Limits — replaced by Rulesets, which we collect"),
    ("contentstorage", "One Loop/Designer storage container — not user content"),
    ("aadpremiumlicenserequired", "Privileged Identity Management — needs Entra ID P2"),
    ("permission grant", "Permission grant policies — not consented"),
]


def plain(message: str) -> str:
    """A readable one-liner for a technical failure message."""
    text = str(message).lower()
    for marker, readable in PLAIN_LANGUAGE:
        if marker in text:
            return readable
    # Fall back to the original, trimmed of the URL noise.
    cleaned = str(message).split(" for url:")[0].strip()
    return cleaned[:160]


def summarise_limits(entries: list) -> list:
    """Collapse repeated per-user limits into one line each."""
    counts = {}
    for message in entries:
        readable = plain(message)
        counts[readable] = counts.get(readable, 0) + 1
    out = []
    for readable, count in counts.items():
        out.append(f"{readable}{f'  ({count} accounts)' if count > 1 else ''}")
    return out


def split_warnings(entry: dict) -> tuple:
    """Separate 'known limitation' from 'something actually went wrong'."""
    expected, unexpected = [], []
    for message in entry.get("warnings", []):
        (expected if is_expected_limit(message) else unexpected).append(message)
    return expected, unexpected


def real_problems(entry: dict) -> list:
    """Errors and warnings that are genuinely worth someone's attention."""
    _, unexpected = split_warnings(entry)
    return list(entry.get("errors", [])) + unexpected


# ---------------------------------------------------------------------------
# Severity
# ---------------------------------------------------------------------------
# A dead credential is the one failure that silently stops everything and
# never fixes itself, so it outranks every other kind of problem and is
# named explicitly rather than buried in a list of warnings.
CREDENTIAL_MARKERS = (
    "invalid_client",
    "invalid_grant",
    "unauthorized_client",
    "aadsts7000215",          # wrong client secret
    "aadsts700016",           # application not found in directory
    "aadsts50034",
    "aadsts900023",           # tenant not found
    "client secret",
    "secret is expired",
    "key is expired",
    "certificate has expired",
    "401",
    "unauthorized",
    "authentication failed",
    "invalid api token",
    "invalid access token",
    "bad_credentials",
    "token is invalid",
    "token expired",
    "could not authenticate",
    "permission denied",
    "access denied",
    "no token",
    "not set in keepass",
    "keepass",
)

CRITICAL = "CRITICAL"
WARNING = "WARNING"
OK = "OK"


def is_credential_failure(message: str) -> bool:
    """Does this failure mean a key, secret or certificate stopped working?"""
    text = str(message).lower()
    if is_expected_limit(text) and not any(
        marker in text for marker in
        ("invalid_client", "aadsts", "secret", "keepass", "token")
    ):
        return False
    return any(marker in text for marker in CREDENTIAL_MARKERS)


def credential_failures(summary: list) -> list:
    """Every credential problem across the run, in plain words."""
    found = []
    for entry in summary:
        for message in real_problems(entry):
            if is_credential_failure(message):
                line = f"{entry['label']}: {plain(message)}"
                if line not in found:
                    found.append(line)
    return found


def severity(summary: list) -> str:
    """CRITICAL, WARNING or OK.

    Licence limits never move this. A run that collected everything the
    tenant is entitled to is a green run, however many things the licence
    does not cover.
    """
    active = [e for e in summary if e["status"] not in ("disabled", "skipped")]
    if not active:
        return WARNING
    if credential_failures(summary):
        return CRITICAL
    if not any(e["records"] for e in active):
        return CRITICAL
    if any(e["status"] == "failed" for e in active):
        return CRITICAL
    if any(real_problems(e) for e in active):
        return WARNING
    return OK


SEVERITY_STYLE = {
    CRITICAL: ("#c0392b", "#fdecea", "CRITICAL", "\u274c"),
    WARNING:  ("#b7791f", "#fffbea", "ATTENTION", "\u26a0\ufe0f"),
    OK:       ("#1e7e34", "#eaf7ee", "ALL GOOD", "\u2705"),
}


def overall_status(summary: list) -> str:
    """Derive one honest word for the whole run.

    Known licence limits do not make a run 'partial' — otherwise every single
    run is flagged and the label stops meaning anything.
    """
    active = [e for e in summary if e["status"] not in ("disabled", "skipped")]
    if not active:
        return "NOTHING TO DO"

    collected_anything = any(e["records"] for e in active)
    problems = any(real_problems(e) for e in active)

    if not collected_anything:
        return "FAILED"
    if problems:
        return "COMPLETED WITH ISSUES"
    return "COMPLETED"


def generate_text_report(report_data: dict, log_file_path: Path, config: dict) -> str:
    """
    Generate a plain-text report from the JSON report data.
    """
    lines = []
    summary = collector_summary(report_data, config)
    status = overall_status(summary)

    lines.append("HonestBackup Run Report")
    lines.append("=======================")
    lines.append(f"Started:  {report_data.get('started_at')}")
    lines.append(f"Finished: {report_data.get('finished_at')}")
    lines.append(f"Status:   {status}")
    lines.append("")

    # --- headline: what actually came back -----------------------------
    total_records = sum(e["records"] for e in summary)
    total_datasets = sum(e["datasets"] for e in summary)
    lines.append(
        f"Collected {total_records} records across {total_datasets} datasets."
    )
    lines.append("")

    lines.append("Backed up")
    lines.append("---------")
    for entry in summary:
        if entry["status"] in ("disabled", "skipped") or not entry["records"]:
            continue
        lines.append(
            f"- {entry['label']}: {entry['records']:,} records "
            f"in {entry['datasets']} datasets"
        )
    lines.append("")

    lines.append("Not backed up (and why)")
    lines.append("-----------------------")
    any_limits = False
    for entry in summary:
        if entry["status"] == "disabled":
            lines.append(f"- {entry['label']}: turned off in settings")
            any_limits = True
            continue
        if entry["status"] == "skipped":
            lines.append(
                f"- {entry['label']}: {entry.get('detail', 'not due yet')}"
            )
            any_limits = True
            continue
        expected, _ = split_warnings(entry)
        for line in summarise_limits(expected):
            lines.append(f"- {entry['label']}: {line}")
            any_limits = True
    if not any_limits:
        lines.append("- nothing; everything available was collected")
    lines.append("")

    problems = []
    for entry in summary:
        for line in summarise_limits(real_problems(entry)):
            problems.append(f"- {entry['label']}: {line}")
        for name, state in entry["stages"].items():
            if state != "ok":
                problems.append(f"- {entry['label']}: {name} — {state}")

    lines.append("Problems")
    lines.append("--------")
    lines.extend(problems or ["- none"])
    lines.append("")

    # --- per-dataset counts, for the record ----------------------------
    lines.append("Datasets collected")
    lines.append("------------------")
    for entry in summary:
        items = entry.get("items") or {}
        if not items:
            continue
        lines.append(f"{entry['label']}:")
        for key, value in sorted(items.items()):
            lines.append(f"    {key}: {value}")
        lines.append("")

    # Archive
    archive = report_data.get('archive', {})
    if archive:
        lines.append("Archive:")
        lines.append(f"  Backup ID: {archive.get('backup_id')}")
        lines.append(f"  File: {archive.get('file')}")
        lines.append(f"  SHA‑256: {archive.get('sha256')}")
        lines.append(f"  Size: {archive.get('size')} bytes")
        uploaded = archive.get('uploaded_to')
        if isinstance(uploaded, list) and uploaded:
            lines.append(f"  Uploaded to: {', '.join(uploaded)}")
    lines.append("")

    # Verification
    verification = report_data.get('verification', {})
    if verification:
        verified = verification.get('verified')
        lines.append(f"Verification: {'YES' if verified else 'NO'}")
    lines.append("")

    # Cleanup
    cleanup = report_data.get('cleanup', {})
    if cleanup:
        removed = cleanup.get('removed', 0)
        lines.append(f"Cleanup: {removed} items removed")
    lines.append("")

    # Log file note
    lines.append(f"Log file: {log_file_path.name}")
    lines.append("")
    lines.append("---")
    lines.append("This report was generated automatically by HonestBackup.")

    return "\n".join(lines)


def send_backup_report(workspace_dir: Path, log_file_path: Path):
    """
    Read the JSON report from the workspace, generate a text report,
    and send it via email (with log attachment) and Telegram (as a document).
    Also sends the log file as a document via Telegram.
    """
    print(f"[reporting] Sending backup report for {workspace_dir}", flush=True)
    report_json = workspace_dir / "backup_report.json"
    if not report_json.is_file():
        raise FileNotFoundError(f"Backup report not found: {report_json}")

    with open(report_json, 'r') as f:
        report_data = json.load(f)

    # Load config for collector enable flags
    config = get_config()

    # Generate the text report
    text_report = generate_text_report(report_data, log_file_path, config)

    # Determine overall status for email subject
    summary = collector_summary(report_data, config)
    status = overall_status(summary)
    started = report_data.get('started_at', 'unknown')
    # Use just the date part for a cleaner subject
    date_part = started.split('T')[0] if 'T' in started else started
    level = severity(summary)
    tag = {CRITICAL: "CRITICAL", WARNING: "ATTENTION", OK: "OK"}[level]
    subject = f"[HonestBackup] {tag} - {status.title()} - {date_part}"

    # A Markdown copy lives beside the JSON report, and rides along with the
    # email so the run is readable long after the mailbox is archived.
    markdown_path = workspace_dir / f"backup-report-{date_part}.md"
    try:
        markdown_path.write_text(build_markdown(report_data, summary),
                                 encoding="utf-8")
    except OSError as exc:
        print(f"[reporting] could not write {markdown_path}: {exc}", flush=True)
        markdown_path = None

    send_email_report(
        subject,
        text_report,
        html_body=build_html(report_data, summary),
        attachments=[str(log_file_path)] + (
            [str(markdown_path)] if markdown_path else []),
    )

    # Send Telegram notifications
    try:
        icon = {
            "COMPLETED": "✅",
            "COMPLETED WITH ISSUES": "⚠️",
            "FAILED": "❌",
            "NOTHING TO DO": "ℹ️",
        }.get(status, "")

        total_records = sum(e["records"] for e in summary)
        archive = report_data.get('archive', {}) or {}

        headline = "Backup complete" if status == "COMPLETED" else (
            "Backup complete — with issues"
            if status == "COMPLETED WITH ISSUES" else f"Backup {status.lower()}"
        )
        parts = [f"{icon} <b>{headline}</b>", date_part, ""]

        # --- what we got -------------------------------------------------
        parts.append("<b>Backed up</b>")
        for entry in summary:
            if entry["status"] in ("disabled", "skipped"):
                continue
            if not entry["records"]:
                continue
            parts.append(
                f"• {entry['label']} — {entry['records']:,} records "
                f"({entry['datasets']} datasets)"
            )

        # --- what we did not, and why ------------------------------------
        not_collected = []
        for entry in summary:
            if entry["status"] == "disabled":
                not_collected.append(f"• {entry['label']} — turned off")
                continue
            if entry["status"] == "skipped":
                not_collected.append(
                    f"• {entry['label']} — {entry.get('detail', 'not due yet')}"
                )
                continue
            expected, _ = split_warnings(entry)
            for line in summarise_limits(expected)[:6]:
                not_collected.append(f"• {line}")

        if not_collected:
            parts.append("")
            parts.append("<b>Not backed up</b>")
            parts.extend(not_collected)

        # --- anything that genuinely went wrong ---------------------------
        problems = []
        for entry in summary:
            for line in summarise_limits(real_problems(entry))[:4]:
                problems.append(f"• {line}")
            broken = [
                name for name, state in entry["stages"].items()
                if state != "ok"
            ]
            for name in broken[:3]:
                problems.append(
                    f"• {entry['label']}: {name} — {entry['stages'][name][:90]}"
                )

        if problems:
            parts.append("")
            parts.append("<b>Problems</b>")
            parts.extend(problems)

        parts.append("")
        parts.append(f"<b>Total:</b> {total_records:,} records")
        if archive.get("size"):
            parts.append(
                f"<b>Archive:</b> {archive.get('backup_id', '?')} "
                f"({archive['size'] / 1048576:.1f} MB)"
            )
        uploaded = archive.get("uploaded_to")
        if isinstance(uploaded, list) and uploaded:
            parts.append(f"<b>Stored:</b> {', '.join(uploaded)}")

        send_telegram_alert("\n".join(parts))

        # Send the log file as a document
        send_telegram_document(str(log_file_path), caption="HonestBackup log file")

        # Send the report as a text document
        report_path = workspace_dir / "backup_report.txt"
        with open(report_path, 'w') as f:
            f.write(text_report)
        send_telegram_document(str(report_path), caption="HonestBackup report")
        # Clean up the temporary report file
        try:
            report_path.unlink()
        except Exception:
            pass
    except Exception as e:
        # Don't let telegram failures break the function; just log
        print(f"[reporting] Failed to send Telegram notifications: {e}", flush=True)

# ---------------------------------------------------------------------------
# The report, in two shapes
# ---------------------------------------------------------------------------
def _facts(report_data: dict, summary: list) -> dict:
    archive = report_data.get("archive", {}) or {}
    started = str(report_data.get("started_at", ""))
    active = [e for e in summary if e["status"] not in ("disabled", "skipped")]

    limits = []
    for entry in summary:
        expected, _ = split_warnings(entry)
        limits.extend(summarise_limits(expected))

    problems = []
    for entry in active:
        for message in real_problems(entry):
            if not is_credential_failure(message):
                line = f"{entry['label']}: {plain(message)}"
                if line not in problems:
                    problems.append(line)

    return {
        "date": started.split("T")[0] if "T" in started else started or "today",
        "time": started.split("T")[1][:5] if "T" in started else "",
        "severity": severity(summary),
        "status": overall_status(summary),
        "records": sum(e["records"] for e in active),
        "collected": active,
        "credentials": credential_failures(summary),
        "problems": problems,
        "limits": sorted(set(limits)),
        "archive": archive,
    }


def build_markdown(report_data: dict, summary: list) -> str:
    """The report as Markdown — attached to the email and kept on disk."""
    f = _facts(report_data, summary)
    _, _, label, icon = SEVERITY_STYLE[f["severity"]]
    out = [
        f"# Backup report - {f['date']}",
        "",
        f"**{icon} {label}** - {f['status'].title()}",
        "",
        f"- Records collected: **{f['records']:,}**",
    ]
    archive = f["archive"]
    if archive.get("size"):
        out.append(f"- Archive size: **{human_size(archive['size'])}**")
    if archive.get("uploaded_to"):
        out.append(f"- Stored in: {', '.join(archive['uploaded_to'])}")
    out.append("")

    if f["credentials"]:
        out += ["## Credentials needing attention", "",
                "These stop collection entirely and will not fix themselves.", ""]
        out += [f"- {line}" for line in f["credentials"]] + [""]

    if f["problems"]:
        out += ["## Problems", ""] + [f"- {line}" for line in f["problems"]] + [""]

    out += ["## What was collected", "",
            "| Service | Records | Datasets | Result |",
            "|---|---:|---:|---|"]
    for entry in f["collected"]:
        out.append(f"| {entry['label']} | {entry['records']:,} | "
                   f"{entry.get('datasets', 0)} | {entry['status']} |")
    out.append("")

    if f["limits"]:
        out += ["## Not collected - licence or plan limits", "",
                "Expected, and not a fault. The tenant's licences do not "
                "cover these.", ""]
        out += [f"- {line}" for line in f["limits"]] + [""]

    return "\n".join(out)


def build_html(report_data: dict, summary: list) -> str:
    """The email body. One column, large text, readable on a phone."""
    f = _facts(report_data, summary)
    colour, tint, label, icon = SEVERITY_STYLE[f["severity"]]
    archive = f["archive"]

    def esc(text):
        return (str(text).replace("&", "&amp;")
                .replace("<", "&lt;").replace(">", "&gt;"))

    def section(title, lines, accent, note=None):
        if not lines:
            return ""
        items = "".join(
            f'<tr><td style="padding:6px 0;border-bottom:1px solid #eee;'
            f'font-size:15px;line-height:1.5;color:#222">{esc(line)}</td></tr>'
            for line in lines
        )
        hint = (f'<p style="margin:0 0 10px;font-size:13px;color:#666">'
                f'{esc(note)}</p>') if note else ""
        return (
            f'<h2 style="margin:26px 0 8px;font-size:16px;color:{accent};'
            f'font-weight:600">{esc(title)}</h2>{hint}'
            f'<table role="presentation" width="100%" cellpadding="0" '
            f'cellspacing="0">{items}</table>'
        )

    rows = "".join(
        f'<tr>'
        f'<td style="padding:10px 0;border-bottom:1px solid #eee;font-size:15px;'
        f'color:#222">{esc(e["label"])}</td>'
        f'<td align="right" style="padding:10px 0;border-bottom:1px solid #eee;'
        f'font-size:15px;color:#222;white-space:nowrap"><b>{e["records"]:,}</b>'
        f'<span style="color:#888"> in {e.get("datasets", 0)}</span></td>'
        f'</tr>'
        for e in f["collected"]
    )

    stored = ""
    if archive.get("size") or archive.get("uploaded_to"):
        bits = []
        if archive.get("size"):
            bits.append(human_size(archive["size"]))
        if archive.get("uploaded_to"):
            bits.append("stored in " + ", ".join(archive["uploaded_to"]))
        stored = (f'<p style="margin:6px 0 0;font-size:14px;color:#666">'
                  f'{esc(" - ".join(bits))}</p>')

    return f"""<!DOCTYPE html>
<html><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Backup report</title></head>
<body style="margin:0;padding:0;background:#f4f5f7;
 -webkit-font-smoothing:antialiased">
<table role="presentation" width="100%" cellpadding="0" cellspacing="0"
 style="background:#f4f5f7"><tr><td align="center" style="padding:20px 12px">
<table role="presentation" width="100%" cellpadding="0" cellspacing="0"
 style="max-width:600px;background:#ffffff;border-radius:10px;
 font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,Arial,sans-serif">

<tr><td style="background:{tint};border-left:5px solid {colour};
 border-radius:10px 10px 0 0;padding:20px 22px">
 <div style="font-size:13px;letter-spacing:.08em;color:{colour};
  font-weight:700">{icon} {label}</div>
 <div style="font-size:22px;color:#111;font-weight:600;margin-top:4px">
  {esc(f['status'].title())}</div>
 <div style="font-size:14px;color:#555;margin-top:2px">
  {esc(f['date'])}{(' at ' + esc(f['time'])) if f['time'] else ''}</div>
</td></tr>

<tr><td style="padding:22px">
 <div style="font-size:30px;font-weight:700;color:#111">{f['records']:,}</div>
 <div style="font-size:14px;color:#666">records collected</div>
 {stored}

 {section("Credentials needing attention", f["credentials"], "#c0392b",
          "These stop collection entirely and will not fix themselves.")}
 {section("Problems", f["problems"], "#b7791f")}

 <h2 style="margin:26px 0 8px;font-size:16px;color:#111;font-weight:600">
  What was collected</h2>
 <table role="presentation" width="100%" cellpadding="0" cellspacing="0">
  {rows}</table>

 {section("Not collected - licence or plan limits", f["limits"], "#666",
          "Expected, and not a fault. Your licences do not cover these.")}
</td></tr>

<tr><td style="padding:16px 22px;border-top:1px solid #eee;
 font-size:12px;color:#888;line-height:1.6">
 HonestBackup - the full log and a Markdown copy of this report are attached.
</td></tr>

</table></td></tr></table></body></html>"""
