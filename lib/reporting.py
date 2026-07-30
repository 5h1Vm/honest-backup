import html
import json
import os
import re
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
            # "Not backed up" overstated this. Every archive tars the whole
            # day's workspace, and an incremental workspace carries the last
            # copy of everything forward — so a collector that was not due
            # this run is still inside the archive this run produced, just
            # not freshly fetched. Saying it was not backed up sent people
            # looking for a gap that is not there.
            entry["status"] = "skipped"
            entry["detail"] = "not due this run - archive still holds the last copy"
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
    "403 client error",
    "failed to resolve table",
    "unavailable: 400",
    "not supported",
    "does not support account owned tokens",
    "account owned tokens",
    "page rule",
    "rate_limits",
    "deprecated",
    "no sites could be listed",
    "empty",
)


# An HTTP status has to be matched as a status, not as three digits that
# happen to sit inside something longer. "collected 5031 records" contains
# "503", and "8ac429f1" contains "429" — read as substrings, both looked like
# a service outage, which quietly filed a real failure under "the far end was
# briefly down" where it no longer raises the headline. Word boundaries keep
# the 503 in "HTTP 503" and reject the one in "5031".
_STATUS_CODE = re.compile(r"\b(\d{3})\b")

EXPECTED_CODES = {"404", "410"}
TRANSIENT_CODES = {"429", "502", "503", "504"}


def status_codes(text: str) -> set:
    """Every three-digit number in the text that stands on its own."""
    return set(_STATUS_CODE.findall(text))


def is_expected_limit(message: str) -> bool:
    text = str(message).lower()
    if any(marker in text for marker in EXPECTED_LIMITS):
        return True
    return bool(status_codes(text) & EXPECTED_CODES)


# A third kind of failure, between "the licence never covered this" and
# "something is broken": the other end was briefly down. It is worth listing —
# that data really is missing from this run — but it is not a fault here, and
# it is usually gone by the next run without anyone touching anything.
TRANSIENT_FAILURES = (
    "service unavailable",
    "server error",
    "bad gateway",
    "gateway timeout",
    "timed out",
    "timeout",
    "temporarily",
    "try again",
    "connection reset",
    "connection aborted",
    "too many requests",
)


def is_transient(message: str) -> bool:
    """Was the other end simply unavailable at the time?"""
    text = str(message).lower()
    if is_expected_limit(text):
        return False
    if status_codes(text) & TRANSIENT_CODES:
        return True
    return any(marker in text for marker in TRANSIENT_FAILURES)


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


def covered_elsewhere(report_data: dict) -> tuple:
    """Markers for failures that another path already made good.

    Graph's /sites endpoint answers 503 on this tenant as a standing fact,
    and the SharePoint REST fallback collects the libraries instead. Listing
    the Graph failure under "not collected" then describes a hole that is
    not there — the files are in the archive, fetched the other way. Only
    suppressed when the fallback actually produced something, so a run where
    both paths fail still says so.
    """
    m365 = (report_data.get("collectors") or {}).get("m365") or {}
    items = m365.get("items") or {}
    covered = []
    if (items.get("sharepoint_rest_files") or 0) > 0:
        covered.append("no sites could be listed")
    return tuple(covered)


def drop_covered(messages: list, covered: tuple) -> list:
    """Remove messages describing a failure something else already handled."""
    if not covered:
        return list(messages)
    return [m for m in messages
            if not any(c in str(m).lower() for c in covered)]


def split_warnings(entry: dict) -> tuple:
    """Sort warnings into limitation, outage, and something actually wrong."""
    expected, transient, unexpected = [], [], []
    for message in entry.get("warnings", []):
        if is_expected_limit(message):
            expected.append(message)
        elif is_transient(message):
            transient.append(message)
        else:
            unexpected.append(message)
    return expected, transient, unexpected


def outages(entry: dict) -> list:
    """Things that failed because the far end was down at the time."""
    _, transient, _ = split_warnings(entry)
    return transient


def real_problems(entry: dict) -> list:
    """Errors and warnings that are genuinely worth someone's attention.

    An outage is deliberately not one of these. It belongs in the report —
    the data is missing and the reader should know — but it says nothing
    about the health of this system, and letting it drive the headline meant
    a Microsoft hiccup announced itself as a backup that went wrong.
    """
    _, _, unexpected = split_warnings(entry)
    errors = [m for m in entry.get("errors", []) if not is_transient(m)]
    return errors + unexpected


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

    Neither licence limits nor upstream outages move this. A run that
    collected everything the tenant is entitled to, and everything the far
    end was willing to hand over that night, is a green run — however many
    things the licence does not cover and however many services were down.
    What is left is the set of failures that will still be there tomorrow
    unless somebody does something.
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

    The headline answers exactly one question: does someone need to act? A
    licence the tenant does not own, and a Microsoft outage that will have
    cleared by morning, both belong in the report — but neither should put
    "with issues" on the front of it. A label that fires on the ordinary run
    is one people learn to ignore, and then it cannot warn them about the
    run that matters. So anything short of a genuine fault reads COMPLETED,
    and everything that did not collect is listed underneath.
    """
    active = [e for e in summary if e["status"] not in ("disabled", "skipped")]
    if not active:
        return "NOTHING TO DO"

    if not any(e["records"] for e in active):
        return "FAILED"
    if severity(summary) == CRITICAL:
        return "COMPLETED WITH ISSUES"
    return "COMPLETED"


def generate_text_report(report_data: dict, log_file_path: Path, config: dict) -> str:
    """
    Generate a plain-text report from the JSON report data.
    """
    lines = []
    summary = collector_summary(report_data, config)
    covered = covered_elsewhere(report_data)
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
        expected, transient, _ = split_warnings(entry)
        for line in summarise_limits(drop_covered(expected, covered)):
            lines.append(f"- {entry['label']}: {line}")
            any_limits = True
        for line in summarise_limits(drop_covered(transient, covered)):
            lines.append(f"- {entry['label']}: {line} (service was down)")
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
    # Both to the terminal and into the run's own log file — printing alone
    # only ever reached whoever happened to be watching a live TUI session
    # at that exact moment. Anyone checking afterward, which is the normal
    # case, saw nothing and had no way to tell whether email or Telegram
    # had even been attempted, let alone whether either succeeded.
    def note(message: str) -> None:
        print(f"[reporting] {message}", flush=True)
        try:
            with open(log_file_path, "a", encoding="utf-8") as f:
                f.write(f"[reporting] {message}\n")
        except OSError:
            pass

    note(f"Sending backup report for {workspace_dir}")
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
        note(f"could not write {markdown_path}: {exc}")
        markdown_path = None

    # Also into the repository's own reports/ folder, under this run's
    # backup id. Everything above only ever reaches an inbox or a phone at
    # the moment it was sent — workspace_dir itself gets cleaned up, so
    # without this a report exists for exactly as long as someone happens
    # to be looking at the message that carried it. reports/ sits beside
    # archives/, hashes/ and manifests/ inside the repository, which the
    # existing Backblaze and office-drive sync already copies wholesale —
    # so a report becomes as durable as the backup it describes, with
    # nothing new to wire up on either end.
    backup_id = (report_data.get("archive") or {}).get("backup_id")
    if backup_id and markdown_path:
        try:
            from storage.config import REPOSITORY_PATH
            reports_dir = Path(REPOSITORY_PATH) / "reports"
            reports_dir.mkdir(parents=True, exist_ok=True)
            (reports_dir / f"{backup_id}.md").write_text(
                markdown_path.read_text(encoding="utf-8"), encoding="utf-8")
            (reports_dir / f"{backup_id}.json").write_text(
                json.dumps(report_data, indent=2), encoding="utf-8")
            note(f"Report preserved in the repository as {backup_id}")
        except OSError as exc:
            note(f"could not preserve the report in the repository: {exc}")

    email_sent = send_email_report(
        subject,
        text_report,
        html_body=build_html(report_data, summary),
        attachments=[str(log_file_path)] + (
            [str(markdown_path)] if markdown_path else []),
    )
    if get_config().get('EMAIL_ENABLED', 'false').lower() == 'true':
        note(f"Email {'sent' if email_sent else 'FAILED to send'} to "
             f"{get_config().get('EMAIL_TO', '(no recipients set)')}")

    # Send Telegram notifications
    telegram_on = get_config().get('TELEGRAM_ENABLED', 'false').lower() == 'true'
    try:
        # Telegram parses the message as HTML, so any < > & that arrives from a
        # collector — a Playwright error saying "<launching> chrome" is the one
        # that bit us — is read as a tag and the whole message is rejected with
        # a 400. Everything interpolated below goes through esc() first; only
        # the <b> tags written here are meant to be markup.
        def esc(value):
            return html.escape(str(value), quote=False)

        covered = covered_elsewhere(report_data)

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
        parts = [f"{icon} <b>{esc(headline)}</b>", esc(date_part), ""]

        # --- what we got -------------------------------------------------
        parts.append("<b>Backed up</b>")
        for entry in summary:
            if entry["status"] in ("disabled", "skipped"):
                continue
            if not entry["records"]:
                continue
            parts.append(
                f"• {esc(entry['label'])} — {entry['records']:,} records "
                f"({entry['datasets']} datasets)"
            )

        # --- what we did not, and why ------------------------------------
        not_collected = []
        for entry in summary:
            if entry["status"] == "disabled":
                not_collected.append(f"• {esc(entry['label'])} — turned off")
                continue
            if entry["status"] == "skipped":
                not_collected.append(
                    f"• {esc(entry['label'])} — "
                    f"{esc(entry.get('detail', 'not due yet'))}"
                )
                continue
            expected, transient, _ = split_warnings(entry)
            for line in summarise_limits(drop_covered(expected, covered))[:6]:
                not_collected.append(f"• {esc(line)}")
            # Outages sit here rather than under Problems: the data really is
            # missing, but nothing on this side needs looking at.
            for line in summarise_limits(drop_covered(
                    transient + [m for m in entry.get("errors", [])
                                 if is_transient(m)], covered))[:4]:
                not_collected.append(f"• {esc(line)} — service was down")

        if not_collected:
            parts.append("")
            parts.append("<b>Not collected this run</b>")
            parts.extend(not_collected)

        # --- anything that genuinely went wrong ---------------------------
        problems = []
        for entry in summary:
            for line in summarise_limits(real_problems(entry))[:4]:
                problems.append(f"• {esc(line)}")
            broken = [
                name for name, state in entry["stages"].items()
                if state != "ok"
            ]
            for name in broken[:3]:
                # Stage errors can be a whole stack trace. One line of it is
                # all a phone notification can carry; the rest is in the log
                # file that follows.
                detail = " ".join(str(entry["stages"][name]).split())[:120]
                problems.append(
                    f"• {esc(entry['label'])}: {esc(name)} — {esc(detail)}"
                )

        if problems:
            parts.append("")
            parts.append("<b>Problems</b>")
            parts.extend(problems)

        parts.append("")
        parts.append(f"<b>Total:</b> {total_records:,} records")
        if archive.get("size"):
            parts.append(
                f"<b>Archive:</b> {esc(archive.get('backup_id', '?'))} "
                f"({archive['size'] / 1048576:.1f} MB)"
            )
        uploaded = archive.get("uploaded_to")
        if isinstance(uploaded, list) and uploaded:
            parts.append(f"<b>Stored:</b> {esc(', '.join(map(str, uploaded)))}")

        # Telegram gets the glance (this message) plus the same report email
        # gets, as a document — asked for explicitly, since email is not
        # always somewhere convenient to check from a phone. The raw log
        # follows on every run, not just a failed one: withholding it until
        # something broke meant the normal answer to "show me last night's
        # run" was nothing at all, and a log nobody can reach for is not
        # much of a record to keep.
        alert_sent = send_telegram_alert("\n".join(parts))
        if telegram_on:
            note(f"Telegram summary {'sent' if alert_sent else 'FAILED to send'}")

        if markdown_path:
            doc_sent = send_telegram_document(
                str(markdown_path), caption=f"HonestBackup report — {date_part}")
            if telegram_on:
                note(f"Telegram report {'sent' if doc_sent else 'FAILED to send'}")

        log_sent = send_telegram_document(
            str(log_file_path),
            caption=("HonestBackup log — run failed" if status == "FAILED"
                     else f"HonestBackup log — {date_part}"))
        if telegram_on:
            note(f"Telegram log {'sent' if log_sent else 'FAILED to send'}")
    except Exception as e:
        # Don't let telegram failures break the function; just log
        note(f"Failed to send Telegram notifications: {e}")

# ---------------------------------------------------------------------------
# The report, in two shapes
# ---------------------------------------------------------------------------
def _facts(report_data: dict, summary: list) -> dict:
    archive = report_data.get("archive", {}) or {}
    started = str(report_data.get("started_at", ""))
    active = [e for e in summary if e["status"] not in ("disabled", "skipped")]

    covered = covered_elsewhere(report_data)
    limits, gaps = [], []
    for entry in summary:
        expected, transient, _ = split_warnings(entry)
        limits.extend(summarise_limits(drop_covered(expected, covered)))
        gaps.extend(summarise_limits(drop_covered(
            transient + [m for m in entry.get("errors", []) if is_transient(m)],
            covered)))

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
        # A collector that was not due, or is switched off, was silently
        # absent from the report — leaving the reader to notice for
        # themselves that a service they expected is simply not mentioned.
        "idle": [
            f"{e['label']} — " + ("turned off" if e["status"] == "disabled"
                                  else e.get("detail", "not due this run"))
            for e in summary if e["status"] in ("disabled", "skipped")
        ],
        "outages": sorted(set(gaps)),
        "limits": sorted(set(limits)),
        "archive": archive,
    }


def headline(f: dict) -> tuple:
    """Colour, tint, label and icon for the banner.

    A green run with nothing missing says ALL GOOD. A green run that lost
    something to an outage is still green — nothing here is broken — but
    saying "all good" above a list of things that did not collect would be
    the report arguing with itself, so it states what happened instead.
    """
    colour, tint, label, icon = SEVERITY_STYLE[f["severity"]]
    if f["severity"] == OK and (f["outages"] or f["limits"]):
        label = "COMPLETED"
    return colour, tint, label, icon


def build_markdown(report_data: dict, summary: list) -> str:
    """The report, laid out to be read exactly as it is written.

    This file is read *raw* in both places it lands. Telegram shows a text
    document's own characters, and no mail client renders a .md attachment
    either. So the pipe tables and ** ** markers this used to carry never
    once reached a renderer that would turn them into something tidy — they
    only ever reached a person, as clutter to read past. A row like

        | Notion | 4,385 | 6 | success |

    is a table only in a program that draws it. Written out plainly and
    aligned by hand it needs no such program, and the email keeps its own
    properly styled HTML body for anyone reading it there.
    """
    f = _facts(report_data, summary)
    _, _, label, icon = headline(f)
    archive = f["archive"]

    when = f["date"] + (f" at {f['time']}" if f["time"] else "")
    out = [
        "HonestBackup - backup report",
        when,
        "",
        f"  {icon}  {label}" + ("" if label.lower() == f["status"].lower()
                                else f" - {f['status'].lower()}"),
        "",
        f"  {f['records']:,} records collected",
    ]
    if archive.get("size"):
        out.append(f"  {human_size(archive['size'])} archive"
                   + (f"  ({archive['backup_id']})"
                      if archive.get("backup_id") else ""))
    if archive.get("uploaded_to"):
        out.append(f"  Stored in {', '.join(archive['uploaded_to'])}")

    def block(title, lines, note=None):
        if not lines:
            return
        out.extend(["", "", title.upper(), "-" * len(title), ""])
        if note:
            out.extend([f"  {note}", ""])
        out.extend(f"  {line}" for line in lines)

    # The urgent things first: a run is read top-down, and a licence limit
    # that has been true for months should not sit above a credential that
    # broke last night.
    block("Credentials needing attention", f["credentials"],
          "These stop collection entirely and will not fix themselves.")
    block("Problems", f["problems"])

    if f["collected"]:
        width = max(len(e["label"]) for e in f["collected"])
        block("What was collected", [
            f"{e['label']:<{width}}   {e['records']:>9,} records"
            f"   {e.get('datasets', 0):>3} datasets   {e['status']}"
            for e in f["collected"]
        ])

    block("Not due this run", f["idle"])

    block("Did not collect this run - service was down", f["outages"],
          "The provider was unavailable at the time. Nothing to fix here; "
          "the next run picks these up.")

    block("Not collected - licence or plan limits", f["limits"],
          "Expected, and not a fault. The tenant's licences do not "
          "cover these.")

    out.append("")
    return "\n".join(out)


def build_html(report_data: dict, summary: list) -> str:
    """The email body. One column, large text, readable on a phone."""
    f = _facts(report_data, summary)
    colour, tint, label, icon = headline(f)
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
 {'' if label.lower() == f['status'].lower() else
   f'''<div style="font-size:22px;color:#111;font-weight:600;margin-top:4px">
  {esc(f['status'].title())}</div>'''}
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

 {section("Not due this run", f["idle"], "#666")}
 {section("Did not collect this run - service was down", f["outages"], "#666",
          "The provider was unavailable at the time. Nothing to fix here; "
          "the next run picks these up.")}
 {section("Not collected - licence or plan limits", f["limits"], "#666",
          "Expected, and not a fault. Your licences do not cover these.")}
</td></tr>

<tr><td style="padding:16px 22px;border-top:1px solid #eee;
 font-size:12px;color:#888;line-height:1.6">
 HonestBackup - the full log and a Markdown copy of this report are attached.
</td></tr>

</table></td></tr></table></body></html>"""
