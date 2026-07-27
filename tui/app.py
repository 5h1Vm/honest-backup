#!/usr/bin/env python3
"""
HonestBackup TUI — a friendly, menu-driven terminal app.

Built for people who are not command-line experts:
- A simple home menu — arrow keys and Enter are all you need.
- Plain language everywhere, no jargon.
- Pure AMOLED black (#000000) theme with cyan accents.

Primary functions
-----------------
- First-time setup (guided wizard)
- Scheduling (what gets backed up, and how often)
- Viewing the logs of past backup runs
- Restoring files from a backup
- Managing the encryption keys
- Running a backup right now

The TUI is a thin facade: backups and restores are delegated to the
existing orchestrator (run as a subprocess, output streamed live).

Launch:  python3 -m orchestrator.run --tui   (or python3 -m tui.app)
"""

from __future__ import annotations

import itertools
import os
import re
import json
import shlex
import shutil
import subprocess
import sys
from datetime import datetime, timedelta
from pathlib import Path

from rich.markup import escape
from rich.text import Text

from textual import on, work
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import (Horizontal, ScrollableContainer,
                                Vertical, VerticalScroll)
from textual.reactive import reactive
from textual.screen import ModalScreen, Screen
from textual.widgets import (
    Button,
    ContentSwitcher,
    DataTable,
    DirectoryTree,
    Input,
    Label,
    OptionList,
    RichLog,
    Select,
    Static,
    Switch,
    TabbedContent,
    TabPane,
    TextArea,
    Tree,
)
from textual.widgets.option_list import Option

# ----------------------------------------------------------------------
# Paths / theme
# ----------------------------------------------------------------------
PROJECT_ROOT = Path(__file__).resolve().parents[1]
CONF_PATH = PROJECT_ROOT / "config" / "backup.conf"
ENV_PATH = PROJECT_ROOT / ".env"
SCHEDULER_STATE = PROJECT_ROOT / "state" / "scheduler" / "state.json"
KEYS_DIR = PROJECT_ROOT / "config" / "keys"
PRIVATE_KEY = KEYS_DIR / "archive.key"
PUBLIC_KEY = KEYS_DIR / "archive.pub"

BLACK = "#000000"
WHITE = "#ffffff"
CYAN = "#00ffff"
GREEN = "#00ff88"
YELLOW = "#ffdd00"
RED = "#ff3355"
MUTED = "#6a6a6a"
PANEL = "#0a0f0f"
BORDER = "#00363a"

SERVICES = [
    ("Microsoft 365", "ENABLE_M365", "m365"),
    ("Cloudflare", "ENABLE_CLOUDFLARE", "cloudflare"),
    ("Notion", "ENABLE_NOTION", "notion"),
]

REQUIRED_ENTRIES = [
    "TENANT_ID", "CLIENT_ID", "CLIENT_SECRET",
    "CLOUDFLARE_API_TOKEN", "ZONE_ID", "CLOUDFLARE_ACCOUNT_ID",
    "NOTION_TOKEN", "B2_KEY_ID", "B2_APPLICATION_KEY",
    "EMAIL_USERNAME", "EMAIL_PASSWORD", "WEBHOOK_HMAC_SECRET",
    "SLACK_WEBHOOK_URL", "TEAMS_WEBHOOK_URL",
    "PAGERDUTY_INTEGRATION_KEY", "TELEGRAM_BOT_TOKEN",
]

CREDENTIAL_PURPOSE = {
    "TENANT_ID": "Microsoft 365 sign-in",
    "CLIENT_ID": "Microsoft 365 sign-in",
    "CLIENT_SECRET": "Microsoft 365 sign-in",
    "CLOUDFLARE_API_TOKEN": "Cloudflare access",
    "ZONE_ID": "Cloudflare zone",
    "CLOUDFLARE_ACCOUNT_ID": "Cloudflare account",
    "NOTION_TOKEN": "Notion access",
    "B2_KEY_ID": "Backblaze storage",
    "B2_APPLICATION_KEY": "Backblaze storage",
    "EMAIL_USERNAME": "Email alerts",
    "EMAIL_PASSWORD": "Email alerts",
    "WEBHOOK_HMAC_SECRET": "Webhook signing",
    "SLACK_WEBHOOK_URL": "Slack alerts",
    "TEAMS_WEBHOOK_URL": "Teams alerts",
    "PAGERDUTY_INTEGRATION_KEY": "PagerDuty alerts",
    "TELEGRAM_BOT_TOKEN": "Telegram alerts",
}


# ----------------------------------------------------------------------
# Helpers (no Textual dependencies)
# ----------------------------------------------------------------------
def read_kv_file(path: Path) -> dict[str, str]:
    data: dict[str, str] = {}
    if not path.exists():
        return data
    try:
        for line in path.read_text().splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            value = value.strip()
            if (value.startswith('"') and value.endswith('"')) or (
                value.startswith("'") and value.endswith("'")
            ):
                value = value[1:-1]
            data[key.strip()] = value
    except OSError:
        pass
    return data


def clean_conf_value(value: str) -> str:
    """Collapse a value onto one line so it cannot inject extra settings.

    run_backup.sh exports every conf line into the shell environment, so a
    pasted newline is worse than a config mixup.
    """
    text = str(value)
    for character in ("\r\n", "\r", "\n", "\x00"):
        text = text.replace(character, " ")
    return text.strip()


def save_conf_values(path: Path, updates: dict[str, str]) -> None:
    """Update KEY=VALUE lines in place, preserving comments and layout."""
    updates = {key: clean_conf_value(value) for key, value in updates.items()}
    remaining = dict(updates)
    out: list[str] = []
    if path.exists():
        for line in path.read_text().splitlines():
            stripped = line.strip()
            if stripped and not stripped.startswith("#") and "=" in stripped:
                key = stripped.split("=", 1)[0].strip()
                if key in remaining:
                    out.append(f"{key}={remaining.pop(key)}")
                    continue
            out.append(line)
    if remaining:
        out.append("")
        out.append("# --- Added via HonestBackup TUI ---")
        for key, value in remaining.items():
            out.append(f"{key}={value}")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(out) + "\n")


def write_env_file(database: str, password: str) -> None:
    content = (
        "# HonestBackup environment\n"
        f"KEEPASS_DATABASE={database}\n"
        f"KEEPASS_PASSWORD={password}\n"
    )
    ENV_PATH.write_text(content)
    os.chmod(ENV_PATH, 0o600)


def human_size(size: float) -> str:
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if size < 1024 or unit == "TB":
            return f"{size:.1f} {unit}" if unit != "B" else f"{int(size)} B"
        size /= 1024
    return f"{size:.1f} TB"


def time_ago(moment: datetime) -> str:
    delta = datetime.now() - moment
    seconds = int(delta.total_seconds())
    if seconds < 60:
        return "just now"
    if seconds < 3600:
        minutes = seconds // 60
        return f"{minutes} minute{'s' if minutes != 1 else ''} ago"
    if seconds < 86400:
        hours = seconds // 3600
        return f"{hours} hour{'s' if hours != 1 else ''} ago"
    days = seconds // 86400
    if days == 1:
        return "yesterday"
    return f"{days} days ago"


def backup_id_to_datetime(backup_id: str) -> datetime | None:
    try:
        return datetime.strptime(backup_id, "%Y-%m-%d_%H-%M-%S")
    except ValueError:
        return None


def vault_dir() -> Path:
    cfg = read_kv_file(CONF_PATH)
    return PROJECT_ROOT / cfg.get("REPOSITORY_PATH", "./backupvault")


def archive_path(backup_id: str) -> Path:
    return vault_dir() / "archives" / f"{backup_id}.tar.zst.age"


def list_local_backups() -> list[tuple[str, str, str]]:
    """(backup_id, friendly timestamp, size) — newest first.

    Guarded throughout: the folder is a setting, and pathlib raises
    PermissionError rather than returning False for one it cannot read.
    """
    rows: list[tuple[str, str, str]] = []
    try:
        archives = vault_dir() / "archives"
        files = sorted(archives.glob("*.tar.zst.age"), reverse=True)
    except OSError:
        return rows
    for file in files:
        backup_id = file.name.replace(".tar.zst.age", "")
        moment = backup_id_to_datetime(backup_id)
        stamp = moment.strftime("%d %b %Y · %H:%M") if moment else backup_id
        try:
            size = human_size(file.stat().st_size)
        except OSError:
            size = "unknown"
        rows.append((backup_id, stamp, size))
    return rows


def office_copy_status() -> str | None:
    """One line describing the copy held on the office drive.

    The office laptop publishes a small status file to the cloud after each
    pull. Reading it needs the network, so callers must do this off the UI
    thread. Returns None when no office copy has ever reported in.
    """
    cfg = read_kv_file(CONF_PATH)
    remote = cfg.get("BACKBLAZE_REMOTE", "").strip().rstrip("/")
    if not remote or cfg.get("BACKBLAZE_ENABLED", "").lower() != "true":
        return None

    try:
        listing = subprocess.run(
            ["rclone", "lsjson", f"{remote}/status", "--files-only"],
            capture_output=True, text=True, timeout=45,
        )
        if listing.returncode != 0:
            return None
        files = [f for f in json.loads(listing.stdout or "[]")
                 if f.get("Name", "").endswith(".json")]
    except (OSError, ValueError, subprocess.SubprocessError):
        return None

    if not files:
        return None

    newest = None
    for entry in files:
        try:
            body = subprocess.run(
                ["rclone", "cat", f"{remote}/status/{entry['Name']}"],
                capture_output=True, text=True, timeout=45,
            )
            report = json.loads(body.stdout)
        except (OSError, ValueError, subprocess.SubprocessError):
            continue
        if newest is None or report.get("last_pull", "") > newest.get("last_pull", ""):
            newest = report

    if not newest:
        return None

    held = newest.get("backups_held", 0)
    site = newest.get("site", "office")
    try:
        when = datetime.fromisoformat(newest["last_pull"]).replace(tzinfo=None)
        seen = time_ago(when)
    except (KeyError, ValueError):
        seen = "unknown"

    if not newest.get("healthy", True):
        return f"[{RED}]{site}: needs attention[/]  [{MUTED}]· checked {seen}[/]"
    plural = "" if held == 1 else "s"
    return (f"[{WHITE}]{site}: {held} backup{plural}[/]  "
            f"[{MUTED}]· copied {seen}[/]")


def load_manifest(backup_id: str) -> dict | None:
    """The manifest describing what a backup contains."""
    path = vault_dir() / "manifests" / f"{backup_id}.manifest.json"
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text())
        return data if isinstance(data, dict) else None
    except (OSError, json.JSONDecodeError):
        return None


def stored_hash(backup_id: str) -> str | None:
    path = vault_dir() / "hashes" / f"{backup_id}.sha256"
    if not path.exists():
        return None
    try:
        return path.read_text().split()[0].strip()
    except (OSError, IndexError):
        return None


def services_in_manifest(manifest: dict) -> list[str]:
    labels = {"m365": "Microsoft 365", "cloudflare": "Cloudflare", "notion": "Notion"}
    found: list[str] = []
    for entry in manifest.get("files", []):
        parts = Path(str(entry.get("path", ""))).parts
        for part in parts[:3]:
            if part in labels and labels[part] not in found:
                found.append(labels[part])
    return found


def list_run_logs() -> list[tuple[Path, datetime, int]]:
    """All backup-run logs and daily reports, newest first: (path, modified, size)."""
    cfg = read_kv_file(CONF_PATH)
    sources: list[tuple[Path, str]] = [
        (PROJECT_ROOT / cfg.get("WORKSPACE", "workspace"), "*.log"),
        (PROJECT_ROOT / cfg.get("LOG_DIR", "logs"), "*.log"),
        (vault_dir() / "reports", "*.md"),
    ]
    seen: set[Path] = set()
    rows: list[tuple[Path, datetime, int]] = []
    for root, pattern in sources:
        if not root.exists():
            continue
        for log in root.rglob(pattern):
            if log in seen:
                continue
            seen.add(log)
            try:
                stat = log.stat()
            except OSError:
                continue
            rows.append((log, datetime.fromtimestamp(stat.st_mtime), stat.st_size))
    rows.sort(key=lambda item: item[1], reverse=True)
    return rows


def log_display_name(path: Path) -> str:
    name = path.stem  # e.g. backup_2026-07-26_18-08-01 or backup
    if path.suffix == ".md":
        return f"Daily summary — {name}"
    if name.startswith("backup_"):
        moment = backup_id_to_datetime(name[len("backup_"):])
        if moment:
            return f"Backup run — {moment.strftime('%d %b %Y at %H:%M:%S')}"
    if name == "backup":
        return f"Daily log — {path.parent.parent.name}"
    return path.name


def colorize(line: str) -> str:
    text = escape(line)
    low = line.lower()
    if "traceback" in low or "error" in low or "failed" in low or "critical" in low:
        return f"[{RED}]{text}[/]"
    if "warn" in low:
        return f"[{YELLOW}]{text}[/]"
    if "success" in low or "completed" in low or "✓" in line:
        return f"[{GREEN}]{text}[/]"
    if low.startswith("debug") or "debug:" in low:
        return f"[{MUTED}]{text}[/]"
    return f"[{WHITE}]{text}[/]"


def schedule_rows() -> list[dict]:
    """Which services are on, and when each last finished.

    There is no per-service interval any more — everything switched on runs
    at every scheduled time, so the only per-service facts are on/off and
    when it last ran.
    """
    cfg = read_kv_file(CONF_PATH)
    try:
        state = json.loads(SCHEDULER_STATE.read_text()) if SCHEDULER_STATE.exists() else {}
    except json.JSONDecodeError:
        state = {}
    rows = []
    for label, enable_key, state_key in SERVICES:
        last_iso = state.get(state_key)
        try:
            last_dt = datetime.fromisoformat(last_iso) if last_iso else None
        except ValueError:
            last_dt = None
        rows.append({
            "label": label,
            "enable_key": enable_key,
            "enabled": cfg.get(enable_key, "false").lower() == "true",
            "last": time_ago(last_dt) if last_dt else "never",
        })
    return rows


_TIME_ROW_SEQUENCE = itertools.count()


def _times_for(cfg: dict, times_key: str) -> list[tuple[int, int]]:
    """The times saved for one service, always at least one box worth."""
    from orchestrator import cron as cron_mod

    try:
        return cron_mod.parse_times(cfg.get(times_key, cron_mod.DEFAULT_TIMES))
    except ValueError:
        return [(1, 0)]


def time_row(service: str, index: int, value: str) -> Horizontal:
    """One editable time, with a button to remove it.

    The id carries a counter, not a position, so removing a middle box
    cannot leave two survivors sharing an id.
    """
    stamp = f"{index}-{next(_TIME_ROW_SEQUENCE)}"
    return Horizontal(
        Input(
            value=value,
            placeholder="13:00",
            max_length=5,
            classes=f"time-input t-{service}",
        ),
        Button("✕", classes=f"remove-time rm-{service}",
               id=f"deltime-{service}-{stamp}"),
        classes="time-cell",
    )


def next_backup_text() -> str:
    """When the next automatic backup will happen, in words."""
    from orchestrator import cron as cron_mod

    cfg = read_kv_file(CONF_PATH)
    if cfg.get("CRON_ENABLED", "false").lower() != "true":
        return "automatic backups are off"

    schedule = cron_mod.service_schedule(cfg)
    if not schedule:
        return "nothing is switched on"

    # The soonest moment any service is due.
    every_time = ",".join(schedule.values())
    upcoming = cron_mod.next_runs(every_time, cfg.get("CRON_TIMEZONE", ""), count=1)
    if not upcoming:
        return "not scheduled"
    return f"{upcoming[0]} ({cron_mod.zone_name(cfg.get('CRON_TIMEZONE', ''))})"


# ======================================================================
# Shared bits
# ======================================================================
def hint_bar(text: str) -> Static:
    return Static(f"[{MUTED}]{text}[/]", classes="hint-bar")


class ArrowNavigation:
    """Makes arrow keys move between controls, not just inside one.

    Textual only moves focus with Tab; arrow keys belong to whichever widget
    holds focus. That leaves buttons unreachable for anyone not using Tab, so
    arrows are handled here instead — but only when the focused widget has no
    use for them:

      text box      keeps left/right for the cursor, gives up up/down
      multi-line    keeps every arrow
      list / table  keeps up/down for rows, gives up left/right
      everything else (buttons, switches)  passes all four through to focus
    """

    FORWARD = ("down", "right")
    BACKWARD = ("up", "left")

    # Every control a person can actually operate, in the order they appear.
    CONTROLS = (
        "Button, Input, Switch, Select, TextArea, "
        "DataTable, OptionList, DirectoryTree, Tree, RichLog"
    )

    def _controls(self) -> list:
        """The controls on this screen, in visual order.

        Built from the DOM rather than Textual's focus_chain: that chain drops
        widgets whose layout has not settled, which silently made the interval
        boxes on the scheduling form unreachable.
        """
        return [
            widget for widget in self.query(self.CONTROLS)
            if widget.display and not widget.disabled
            and getattr(widget, "can_focus", True)
        ]

    def step_focus(self, forward: bool) -> None:
        """Focus the next control, skipping the containers that wrap them.

        Not named _move_focus: Textual's Screen has one of its own with a
        different signature, and shadowing it breaks Tab.
        """
        chain = self._controls()
        if not chain:
            return

        # Match on identity — widget equality is not reliable for this.
        focused = self.focused
        index = next(
            (i for i, widget in enumerate(chain) if widget is focused), None
        )
        if index is None:
            self.set_focus(chain[0 if forward else -1])
            return

        step = 1 if forward else -1
        self.set_focus(chain[(index + step) % len(chain)])

    def on_key(self, event) -> None:
        key = event.key
        if key not in self.FORWARD + self.BACKWARD:
            return

        focused = self.focused

        # A multi-line editor needs every arrow key.
        if isinstance(focused, TextArea):
            return
        # A single-line box needs left/right for the text cursor.
        if isinstance(focused, Input) and key in ("left", "right"):
            return
        # Lists and tables need up/down to move through their rows.
        if isinstance(focused, (DataTable, OptionList, Select)) and \
                key in ("up", "down"):
            return

        self.step_focus(forward=key in self.FORWARD)

        event.stop()
        event.prevent_default()


class NavDataTable(DataTable):
    """A row-oriented table that lets the arrow keys leave it.

    A plain DataTable swallows every arrow key, so a keyboard user who lands
    in a table can never reach the buttons underneath. These tables show rows,
    not editable cells, so left/right have nothing to do inside them — they
    step out instead. Up and down still move through the rows, and step out
    when you reach the top or the bottom.
    """

    BINDINGS = [
        Binding("left", "leave_backward", "Back", show=False),
        Binding("right", "leave_forward", "Next", show=False),
    ]

    def _step(self, forward: bool) -> None:
        # Only ArrowNavigation screens have step_focus. Anywhere else, fall
        # back to Textual's own focus movement.
        mover = getattr(self.screen, "step_focus", None)
        if callable(mover):
            mover(forward=forward)
        elif forward:
            self.screen.focus_next()
        else:
            self.screen.focus_previous()

    def action_leave_backward(self) -> None:
        self._step(forward=False)

    def action_leave_forward(self) -> None:
        self._step(forward=True)

    def action_cursor_down(self) -> None:
        if self.cursor_row >= self.row_count - 1:
            self._step(forward=True)
            return
        super().action_cursor_down()

    def action_cursor_up(self) -> None:
        if self.cursor_row <= 0:
            self._step(forward=False)
            return
        super().action_cursor_up()


class NavScreen(ArrowNavigation, Screen):
    """A screen you can drive entirely with the arrow keys and Enter."""


class NavModal(ArrowNavigation, ModalScreen):
    """A dialog you can drive entirely with the arrow keys and Enter."""


class TitleBar(Static):
    """One-line screen title: '█ HONESTBACKUP · <section>'."""

    def __init__(self, section: str) -> None:
        super().__init__(
            f"[b {WHITE}]█ HONESTBACKUP[/]  [{MUTED}]·[/]  [b {CYAN}]{section}[/]",
            classes="title-bar",
        )


class ConfirmScreen(NavModal):
    """A simple Yes/No question."""

    BINDINGS = [Binding("escape", "no", "No", priority=True)]

    def __init__(self, title: str, message: str, yes_label: str = "Yes, continue") -> None:
        super().__init__()
        self._title = title
        self._message = message
        self._yes_label = yes_label

    def compose(self) -> ComposeResult:
        with Vertical(id="confirm-dialog", classes="dialog"):
            yield Static(f"[b]{self._title}[/b]", classes="dialog-title")
            yield Static(self._message, classes="dialog-body")
            with Horizontal(classes="dialog-buttons"):
                yield Button(self._yes_label, id="confirm-yes", variant="error")
                yield Button("Go back", id="confirm-no", variant="primary")

    @on(Button.Pressed, "#confirm-yes")
    def _yes(self) -> None:
        self.dismiss(True)

    @on(Button.Pressed, "#confirm-no")
    def _no(self) -> None:
        self.dismiss(False)

    def action_no(self) -> None:
        self.dismiss(False)


# ======================================================================
# Home
# ======================================================================
MENU_ITEMS: list[tuple[str, str, str]] = [
    # (id, title, description)
    ("backup", "Back up now", "Make a backup right away"),
    ("backups", "My backups", "See what you have, check it, look inside"),
    ("restore", "Restore files", "Bring files back from a saved backup"),
    ("logs", "Logs & reports", "Read what happened during past backup runs"),
    ("browse", "Read backed-up data", "Open restored files — JSON shown in a clean viewer"),
    ("schedule", "Scheduling", "Choose what gets backed up, and how often"),
    ("settings", "Settings", "Storage, notifications and other options"),
    ("credentials", "API keys & passwords", "Add or change the credentials the backup uses"),
    ("keys", "Encryption keys", "View or replace the keys that lock your backups"),
    ("setup", "First-time setup", "A guided walk-through in six easy steps"),
    ("help", "Help", "What each part does, and the keys that work anywhere"),
    ("quit", "Quit", "Close HonestBackup"),
]


class HomeScreen(NavScreen):
    """The main menu. Everything starts here."""

    BINDINGS = [
        Binding("q", "quit_app", "Quit", show=False),
        Binding("question_mark", "help", "Help", show=False),
        Binding("ctrl+p", "help", "Help", priority=True, show=False),
    ]

    def compose(self) -> ComposeResult:
        with Horizontal(id="home-header"):
            yield Static("", id="home-brand")
            yield Static("", id="home-clock")
        yield Static("", id="home-status")
        yield OptionList(id="home-menu")
        yield hint_bar("↑ ↓ move · Enter opens · 1-9 jump to an item · q quit · ? help")

    def on_mount(self) -> None:
        self._status_lines: list[str] = []
        self._office_line: str | None = None
        self._refresh_header()
        self.set_interval(1.0, self._refresh_header)
        self._rebuild()

    def on_screen_resume(self) -> None:
        self._rebuild()

    def _paint_status(self) -> None:
        lines = list(self._status_lines)
        if self._office_line:
            lines.append(f"[{MUTED}]Office copy[/]   {self._office_line}")
        body = "\n".join(lines)
        self.query_one("#home-status", Static).update(f"\n{body}\n")

    @work(thread=True, exclusive=True, group="office-status")
    def _load_office_status(self) -> None:
        """Fetch the office drive's status without blocking the menu."""
        line = office_copy_status()
        if line:
            self.app.call_from_thread(self._show_office_status, line)

    def _show_office_status(self, line: str) -> None:
        self._office_line = line
        self._paint_status()

    def _refresh_header(self) -> None:
        app: HonestbackupTUI = self.app  # type: ignore[assignment]
        state = (
            f"[{YELLOW}]● working[/]" if app.busy else f"[{GREEN}]● ready[/]"
        )
        self.query_one("#home-brand", Static).update(
            f"[b {WHITE}]█ HONESTBACKUP[/]  {state}"
        )
        self.query_one("#home-clock", Static).update(
            f"[{WHITE}]{datetime.now().strftime('%A %d %B · %H:%M:%S')}[/]"
        )

    def _rebuild(self) -> None:
        app: HonestbackupTUI = self.app  # type: ignore[assignment]

        # Status card
        backups = list_local_backups()
        if backups:
            last_dt = backup_id_to_datetime(backups[0][0])
            last_text = time_ago(last_dt) if last_dt else backups[0][1]
            last_line = (
                f"[{MUTED}]Last backup[/]   [{WHITE}]{last_text}[/]  "
                f"[{MUTED}]· {len(backups)} backup"
                f"{'s' if len(backups) != 1 else ''} saved[/]"
            )
        else:
            last_line = f"[{MUTED}]Last backup[/]   [{YELLOW}]none yet[/]"
        due_line = f"[{MUTED}]Next backup[/]   [{WHITE}]{next_backup_text()}[/]"
        self._status_lines = [last_line, due_line]
        self._paint_status()
        self._load_office_status()

        # Menu
        menu = self.query_one("#home-menu", OptionList)
        highlighted = menu.highlighted
        menu.clear_options()
        items = list(MENU_ITEMS)
        if app.busy:
            items.insert(0, ("progress", "Show progress",
                             f"{app.job_name or 'A job'} is running — watch it live"))
        # Only the first nine get a number, because only 1-9 are shortcuts.
        # Numbering the rest would promise a key that does nothing.
        for index, (item_id, title, description) in enumerate(items):
            lead = f"{index + 1}.  " if index < 9 else "    "
            prompt = Text.from_markup(
                f"[b {WHITE}]{lead}{title}[/]\n    [{MUTED}]{description}[/]"
            )
            menu.add_option(Option(prompt, id=item_id))
        menu.highlighted = highlighted if highlighted is not None else 0
        menu.focus()

    @on(OptionList.OptionSelected, "#home-menu")
    def _selected(self, event: OptionList.OptionSelected) -> None:
        self._open(event.option_id or "")

    def on_key(self, event) -> None:
        # Number shortcuts: 1-9 open the first nine items directly.
        if event.key and event.key in "123456789":
            menu = self.query_one("#home-menu", OptionList)
            index = int(event.key) - 1
            if index < menu.option_count:
                option = menu.get_option_at_index(index)
                self._open(option.id or "")
                event.stop()

    def _open(self, item_id: str) -> None:
        app: HonestbackupTUI = self.app  # type: ignore[assignment]
        if item_id == "backup":
            app.push_screen(BackupOptionsScreen(), app.backup_mode_chosen)
        elif item_id == "help":
            app.push_screen(HelpScreen())
        elif item_id == "backups":
            app.push_screen(BackupsScreen())
        elif item_id == "progress":
            app.push_screen(ActivityScreen())
        elif item_id == "schedule":
            app.push_screen(ScheduleScreen())
        elif item_id == "restore":
            app.push_screen(RestoreWizardScreen(), app.restore_confirmed)
        elif item_id == "logs":
            app.push_screen(LogsScreen())
        elif item_id == "browse":
            app.push_screen(FileBrowserScreen())
        elif item_id == "settings":
            app.push_screen(SettingsScreen())
        elif item_id == "setup":
            app.push_screen(SetupWizardScreen())
        elif item_id == "credentials":
            app.push_screen(CredentialsScreen())
        elif item_id == "keys":
            app.push_screen(KeysScreen())
        elif item_id == "quit":
            app.exit()

    def action_quit_app(self) -> None:
        self.app.exit()

    def action_help(self) -> None:
        self.app.push_screen(HelpScreen())


# ======================================================================
# Activity (live job output)
# ======================================================================
class ActivityScreen(NavScreen):
    """Shows the output of the running (or last) job, live."""

    BINDINGS = [Binding("escape", "back", "Back", priority=True)]

    def compose(self) -> ComposeResult:
        app: HonestbackupTUI = self.app  # type: ignore[assignment]
        yield TitleBar(app.job_name or "Activity")
        yield RichLog(id="activity-log", markup=True, wrap=True, auto_scroll=True)
        with Horizontal(classes="button-row"):
            yield Button("← Back to menu (keeps running)", id="activity-back", variant="primary")
            yield Button("Stop this job", id="activity-cancel", variant="error")
        yield hint_bar("Esc goes back to the menu — the job keeps running in the background")

    def on_mount(self) -> None:
        app: HonestbackupTUI = self.app  # type: ignore[assignment]
        log = self.query_one("#activity-log", RichLog)
        for line in app.output_lines:
            log.write(line)
        self.query_one("#activity-cancel", Button).disabled = not app.busy

    def append(self, line: str) -> None:
        self.query_one("#activity-log", RichLog).write(line)

    def job_done(self) -> None:
        self.query_one("#activity-cancel", Button).disabled = True

    @on(Button.Pressed, "#activity-back")
    def _back_btn(self) -> None:
        self.app.pop_screen()

    @on(Button.Pressed, "#activity-cancel")
    def _cancel(self) -> None:
        app: HonestbackupTUI = self.app  # type: ignore[assignment]
        app.cancel_job()

    def action_back(self) -> None:
        self.app.pop_screen()


# ======================================================================
# Backup options (how to run the backup)
# ======================================================================
class BackupOptionsScreen(NavModal):
    """Ask how the backup should run, in plain words."""

    BINDINGS = [Binding("escape", "close", "Close", priority=True)]

    def compose(self) -> ComposeResult:
        with Vertical(id="backup-options-dialog", classes="dialog"):
            yield Static("[b]BACK UP NOW[/b] · How would you like to run it?",
                         classes="dialog-title")
            with Vertical(id="backup-options-list"):
                yield Button(
                    "Back up everything now   (recommended)",
                    id="opt-force", variant="primary",
                )
                yield Static(
                    f"[{MUTED}]Backs up every service that is turned on, "
                    f"even if it ran recently.[/]\n", classes="option-note",
                )
                yield Button("Back up only what is due", id="opt-due")
                yield Static(
                    f"[{MUTED}]Follows your schedule — services backed up "
                    f"recently are skipped.[/]\n", classes="option-note",
                )
                yield Button("Space-saving backup", id="opt-incremental")
                yield Static(
                    f"[{MUTED}]Backs up everything, but stores only what "
                    f"changed since last time.[/]\n", classes="option-note",
                )
            with Horizontal(classes="dialog-buttons"):
                yield Button("Cancel", id="opt-cancel", variant="error")

    @on(Button.Pressed, "#opt-force")
    def _force(self) -> None:
        self.dismiss("force")

    @on(Button.Pressed, "#opt-due")
    def _due(self) -> None:
        self.dismiss("due")

    @on(Button.Pressed, "#opt-incremental")
    def _incremental(self) -> None:
        self.dismiss("incremental")

    @on(Button.Pressed, "#opt-cancel")
    def _cancel(self) -> None:
        self.dismiss(None)

    def action_close(self) -> None:
        self.dismiss(None)


# ======================================================================
# My backups (vault browser)
# ======================================================================
class BackupsScreen(NavScreen):
    """Browse the backup vault: every saved backup, newest first."""

    BINDINGS = [Binding("escape", "back", "Back", priority=True)]

    def compose(self) -> ComposeResult:
        yield TitleBar("My backups")
        yield Static("", id="backups-intro")
        yield NavDataTable(id="backups-table")
        yield Static("", id="backups-cloud")
        with Horizontal(classes="button-row"):
            yield Button("Open selected backup", id="backups-open", variant="primary")
            yield Button("Check the cloud copy", id="backups-check")
            yield Button("← Back", id="backups-back")
        yield hint_bar("↑ ↓ choose a backup · Enter opens it · ← → reach the buttons · Esc back")

    @on(Button.Pressed, "#backups-check")
    def _check_cloud(self) -> None:
        self.query_one("#backups-cloud", Static).update(
            f"\n[{MUTED}]Checking the cloud against the ledger…[/]\n"
        )
        self._cloud_worker()

    @work(thread=True, exclusive=True, group="cloud-check")
    def _cloud_worker(self) -> None:
        from storage import ledger
        from storage.config import REPOSITORY_PATH, BACKBLAZE_REMOTE

        root = Path(REPOSITORY_PATH)
        record = ledger.load(root)
        result = ledger.check(root, BACKBLAZE_REMOTE)
        self.app.call_from_thread(self._show_cloud, record, result)

    def _show_cloud(self, record: dict, result) -> None:
        colour = GREEN if result.healthy else RED
        lines = [
            f"\n[b {colour}]{result.summary}[/]",
            f"[{MUTED}]Ledger last updated {record.get('updated', 'never')}[/]",
        ]
        for label, entries in (("Missing", result.missing),
                               ("Wrong size", result.mismatched)):
            for item in entries[:4]:
                lines.append(f"[{RED}]{label}: {item}[/]")
            if len(entries) > 4:
                lines.append(f"[{MUTED}]…and {len(entries) - 4} more[/]")
        self.query_one("#backups-cloud", Static).update("\n".join(lines) + "\n")

    def on_mount(self) -> None:
        table = self.query_one("#backups-table", DataTable)
        table.cursor_type = "row"
        table.add_columns("When", "Size", "Contains", "Files")
        backups = list_local_backups()
        total = 0
        for backup_id, stamp, size in backups:
            manifest = load_manifest(backup_id)
            services = ", ".join(services_in_manifest(manifest)) if manifest else "—"
            files = str(manifest.get("file_count", "—")) if manifest else "—"
            table.add_row(stamp, size, services or "—", files, key=backup_id)
            try:
                total += archive_path(backup_id).stat().st_size
            except OSError:
                pass
        intro = (
            f"\n[{WHITE}]You have [b]{len(backups)}[/b] backups, using "
            f"[b]{human_size(total)}[/b] of space. Open one to check it or "
            f"look inside.[/]\n"
            if backups
            else f"\n[{YELLOW}]No backups yet — choose “Back up now” from the menu.[/]\n"
        )
        self.query_one("#backups-intro", Static).update(intro)
        if not backups:
            self.query_one("#backups-open", Button).disabled = True
        table.focus()

    @on(DataTable.RowSelected, "#backups-table")
    def _row_selected(self, event: DataTable.RowSelected) -> None:
        if event.row_key is not None:
            self.app.push_screen(BackupDetailScreen(str(event.row_key.value)))

    @on(Button.Pressed, "#backups-open")
    def _open_btn(self) -> None:
        table = self.query_one("#backups-table", DataTable)
        if table.row_count:
            row_key, _ = table.coordinate_to_cell_key((table.cursor_row, 0))
            if row_key is not None:
                self.app.push_screen(BackupDetailScreen(str(row_key.value)))

    @on(Button.Pressed, "#backups-back")
    def _back_btn(self) -> None:
        self.app.pop_screen()

    def action_back(self) -> None:
        self.app.pop_screen()


class BackupDetailScreen(NavScreen):
    """One backup: what it holds, whether it is intact, and actions."""

    BINDINGS = [Binding("escape", "back", "Back", priority=True)]

    def __init__(self, backup_id: str) -> None:
        super().__init__()
        self.backup_id = backup_id

    def compose(self) -> ComposeResult:
        moment = backup_id_to_datetime(self.backup_id)
        title = moment.strftime("Backup from %d %b %Y at %H:%M") if moment else self.backup_id
        yield TitleBar(title)
        with VerticalScroll(id="backup-detail-body"):
            yield Static("", id="backup-detail-info")
            yield Static("", id="backup-verify-result")
        with Horizontal(classes="button-row"):
            yield Button("Check it is intact", id="detail-verify", variant="primary")
            yield Button("See what's inside", id="detail-contents")
            yield Button("Restore from this backup", id="detail-restore", variant="success")
            yield Button("← Back", id="detail-back")
        yield hint_bar("↑ ↓ ← → move between buttons · Enter presses · Esc goes back")

    def on_mount(self) -> None:
        manifest = load_manifest(self.backup_id)
        expected = stored_hash(self.backup_id)
        archive = archive_path(self.backup_id)
        size = human_size(archive.stat().st_size) if archive.exists() else "missing!"
        if manifest:
            services = ", ".join(services_in_manifest(manifest)) or "—"
            files = manifest.get("file_count", "?")
            content = human_size(float(manifest.get("total_size", 0)))
        else:
            services, files, content = "—", "—", "—"
        self.query_one("#backup-detail-info", Static).update(
            f"""
[{MUTED}]Contains[/]        [{WHITE}]{services}[/]
[{MUTED}]Files inside[/]    [{WHITE}]{files}[/]  [{MUTED}]({content} before packing)[/]
[{MUTED}]Size on disk[/]    [{WHITE}]{size}[/]  [{MUTED}](packed and locked)[/]
[{MUTED}]Fingerprint[/]     [{WHITE}]{expected or '— not recorded —'}[/]

[{MUTED}]“Check it is intact” re-reads the whole backup and makes sure
it still matches the fingerprint recorded when it was made.[/]
"""
        )
        if not archive.exists():
            self.query_one("#detail-verify", Button).disabled = True
            self.query_one("#detail-restore", Button).disabled = True
        if not manifest:
            self.query_one("#detail-contents", Button).disabled = True

    @on(Button.Pressed, "#detail-back")
    def _back_btn(self) -> None:
        self.app.pop_screen()

    def action_back(self) -> None:
        self.app.pop_screen()

    @on(Button.Pressed, "#detail-contents")
    def _contents(self) -> None:
        self.app.push_screen(BackupContentsScreen(self.backup_id))

    @on(Button.Pressed, "#detail-restore")
    def _restore(self) -> None:
        app: HonestbackupTUI = self.app  # type: ignore[assignment]
        app.push_screen(
            RestoreWizardScreen(preselected=self.backup_id), app.restore_confirmed
        )

    @on(Button.Pressed, "#detail-verify")
    def _verify(self) -> None:
        self.query_one("#detail-verify", Button).disabled = True
        self.query_one("#backup-verify-result", Static).update(
            f"[{CYAN}]Checking — this reads the whole backup, please wait…[/]"
        )
        self._verify_worker()

    @work(thread=True, exclusive=True, group="verify")
    def _verify_worker(self) -> None:
        import hashlib

        expected = stored_hash(self.backup_id)
        digest = hashlib.sha256()
        try:
            with open(archive_path(self.backup_id), "rb") as handle:
                for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                    digest.update(chunk)
            actual: str | None = digest.hexdigest()
            error = None
        except OSError as exc:
            actual, error = None, str(exc)

        def render() -> None:
            result = self.query_one("#backup-verify-result", Static)
            self.query_one("#detail-verify", Button).disabled = False
            if error:
                result.update(f"[{RED}]✗ Could not read the backup: {escape(error)}[/]")
            elif expected is None:
                result.update(
                    f"[{YELLOW}]⚠ No fingerprint was recorded for this backup, "
                    f"so it cannot be checked.[/]"
                )
            elif actual == expected:
                result.update(
                    f"[b {GREEN}]✓ All good — this backup is intact and untouched.[/]"
                )
            else:
                result.update(
                    f"[b {RED}]✗ Warning: this backup does NOT match its "
                    f"fingerprint. It may be damaged or altered.[/]"
                )

        self.app.call_from_thread(render)


def extract_one_file(backup_id: str, inner_path: str) -> tuple[bytes | None, str]:
    """Pull a single file out of an encrypted backup, without unpacking it all.

    age and zstd stream, and tar stops reading once it has found the member,
    so this costs a fraction of a second even on a large archive. Returns
    (contents, error message).
    """
    archive = archive_path(backup_id)
    if not archive.exists():
        return None, "the archive for this backup is not on this computer"
    if not PRIVATE_KEY.exists():
        return None, f"the key is missing ({PRIVATE_KEY.name})"

    # Everything in the tar sits under a folder named for the backup's day.
    member = f"{backup_id[:10]}/{inner_path}"
    pipeline = (
        f"age -d -i {shlex.quote(str(PRIVATE_KEY))} {shlex.quote(str(archive))} "
        f"| zstd -dc | tar -xO -f - {shlex.quote(member)}"
    )
    try:
        done = subprocess.run(["bash", "-c", pipeline],
                              capture_output=True, timeout=180)
    except (OSError, subprocess.SubprocessError) as exc:
        return None, str(exc)
    if not done.stdout:
        detail = (done.stderr or b"").decode("utf-8", "replace").strip()
        return None, detail.splitlines()[-1] if detail else "file not found in the archive"
    return done.stdout, ""


def preview_bytes(name: str, blob: bytes, log: RichLog) -> None:
    """Render bytes pulled straight out of an archive."""
    log.clear()
    suffix = Path(name).suffix.lower()

    def heading():
        leaf = Path(name).name
        folder = str(Path(name).parent)
        log.write(f"[b {CYAN}]{escape(leaf)}[/]  [{MUTED}]{human_size(len(blob))}[/]")
        if folder not in (".", ""):
            log.write(f"[{MUTED}]{escape(folder)}[/]")
        log.write("")

    if suffix in BINARY_SUFFIXES:
        heading()
        log.write(f"[{YELLOW}]This kind of file cannot be shown as text.[/]")
        log.write(f"[{MUTED}]Restore the backup to open it in its own program.[/]")
        return

    try:
        text = blob.decode("utf-8")
    except UnicodeDecodeError:
        heading()
        log.write(f"[{YELLOW}]This file is not text.[/]")
        return

    if len(blob) > MAX_PREVIEW_BYTES:
        text = text[:MAX_PREVIEW_BYTES]
        truncated = True
    else:
        truncated = False

    heading()
    if suffix == ".json":
        try:
            from rich.json import JSON as RichJSON
            log.write(RichJSON(text, indent=2))
        except Exception:
            log.write(escape(text))
    else:
        log.write(escape(text))
    if truncated:
        log.write(f"\n[{MUTED}]… shown up to {human_size(MAX_PREVIEW_BYTES)}.[/]")


BINARY_SUFFIXES = {
    ".docx", ".xlsx", ".pptx", ".pdf", ".png", ".jpg", ".jpeg", ".gif",
    ".zip", ".age", ".zst", ".tar", ".kdbx", ".ico", ".woff", ".woff2",
}


class BackupContentsScreen(NavScreen):
    """The files inside one backup, as a tree, with a viewer.

    The archive stays encrypted on disk. Selecting a file decrypts just that
    one file out of it, which takes a fraction of a second.
    """

    BINDINGS = [Binding("escape", "back", "Back", priority=True)]

    def __init__(self, backup_id: str) -> None:
        super().__init__()
        self.backup_id = backup_id
        self._manifest = load_manifest(backup_id)

    def compose(self) -> ComposeResult:
        yield TitleBar(f"Inside the backup · {self.backup_id}")
        if not self._manifest:
            yield Static(
                f"\n[{YELLOW}]No contents list was recorded for this backup.[/]",
                id="contents-empty",
            )
        else:
            files = self._manifest.get("files", [])
            total = human_size(float(self._manifest.get("total_size", 0)))
            yield Static(
                f"\n[{WHITE}]{len(files)} file{'' if len(files) == 1 else 's'} · "
                f"{total}[/]  [{MUTED}]— choose one to read it; it is decrypted "
                f"only to show you[/]\n",
                id="contents-summary",
            )
            with Horizontal(id="contents-split"):
                yield Tree("backup", id="contents-tree")
                yield RichLog(id="contents-preview", markup=True, wrap=True,
                              auto_scroll=False)
        with Horizontal(classes="button-row"):
            yield Button("← Back", id="contents-back", variant="primary")
        yield hint_bar(
            "↑ ↓ move · Enter opens a folder or reads a file · "
            "← → reach the buttons · Esc goes back"
        )

    def on_mount(self) -> None:
        if not self._manifest:
            return
        tree = self.query_one("#contents-tree", Tree)
        tree.root.label = f"[b {CYAN}]{self.backup_id}[/]"
        tree.root.expand()
        self._build_tree(tree)
        self.query_one("#contents-preview", RichLog).write(
            f"[{MUTED}]Choose a file on the left to read it.[/]"
        )
        tree.focus()

    def _build_tree(self, tree: Tree) -> None:
        """Turn the manifest's flat paths back into the folders they came from."""
        folders: dict[str, object] = {"": tree.root}

        def folder_for(path: str):
            if path in folders:
                return folders[path]
            parent_path, _, name = path.rpartition("/")
            parent = folder_for(parent_path)
            node = parent.add(f"[{CYAN}]{escape(name)}/[/]", expand=False)
            folders[path] = node
            return node

        entries = sorted(
            self._manifest.get("files", []),
            key=lambda e: str(e.get("path", "")),
        )
        for entry in entries:
            path = str(entry.get("path", "")).strip("/")
            if not path:
                continue
            parent_path, _, name = path.rpartition("/")
            size = human_size(float(entry.get("size", 0)))
            folder_for(parent_path).add_leaf(
                f"[{WHITE}]{escape(name)}[/]  [{MUTED}]{size}[/]",
                data=path,
            )

    @on(Tree.NodeSelected, "#contents-tree")
    def _chosen(self, event: Tree.NodeSelected) -> None:
        path = event.node.data
        if not path:
            return          # a folder; Textual expands it for us
        preview = self.query_one("#contents-preview", RichLog)
        preview.clear()
        preview.write(f"[{MUTED}]Opening {escape(Path(path).name)}…[/]")
        self._open_file(path)

    @work(thread=True, exclusive=True, group="contents-preview")
    def _open_file(self, path: str) -> None:
        blob, problem = extract_one_file(self.backup_id, path)
        self.app.call_from_thread(self._show_file, path, blob, problem)

    def _show_file(self, path: str, blob: bytes | None, problem: str) -> None:
        preview = self.query_one("#contents-preview", RichLog)
        if blob is None:
            preview.clear()
            preview.write(f"[{RED}]Could not open {escape(Path(path).name)}[/]\n")
            preview.write(f"[{MUTED}]{escape(problem)}[/]")
            return
        preview_bytes(path, blob, preview)

    @on(Button.Pressed, "#contents-back")
    def _back_btn(self) -> None:
        self.app.pop_screen()

    def action_back(self) -> None:
        self.app.pop_screen()


# ======================================================================
# Browse backed-up data (with JSON viewer)
# ======================================================================
DAY_DIR_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def browse_roots() -> list[tuple[str, Path]]:
    """Places where readable (decrypted) backup data lives."""
    cfg = read_kv_file(CONF_PATH)
    roots: list[tuple[str, Path]] = []
    restore_root = PROJECT_ROOT / cfg.get("RESTORE_DIR", "restore")
    if restore_root.exists() and any(restore_root.iterdir()):
        roots.append(("Restored files", restore_root))
    for extra in sorted(PROJECT_ROOT.glob("*restore*")):
        if extra.is_dir() and extra != restore_root and any(extra.iterdir()):
            roots.append((f"Restored files ({extra.name})", extra))
    workspace = PROJECT_ROOT / cfg.get("WORKSPACE", "workspace")
    if workspace.exists():
        for day in sorted(workspace.iterdir(), reverse=True):
            # Only dated folders. "archive" is the staging area holding the
            # encrypted tarballs, which are not readable and not data.
            if day.is_dir() and DAY_DIR_RE.match(day.name):
                roots.append((f"Collected data — {day.name}", day))
    return roots


MAX_PREVIEW_BYTES = 2_000_000


def preview_file(path: Path, log: RichLog) -> None:
    """Render a file into the preview pane — pretty JSON when possible."""
    log.clear()
    try:
        size = path.stat().st_size
    except OSError as exc:
        log.write(f"[{RED}]Could not open: {escape(str(exc))}[/]")
        return
    if path.suffix.lower() == ".zip":
        log.write(
            f"[{CYAN}]This is a zip ({human_size(size)}) — press Enter on it "
            f"to open it and read the files inside.[/]"
        )
        return
    if path.suffix.lower() in (".age", ".zst", ".tar", ".kdbx", ".png", ".jpg"):
        log.write(
            f"[{YELLOW}]This is a packed file ({human_size(size)}) — "
            f"it cannot be shown as text.[/]"
        )
        return
    try:
        with open(path, "r", errors="replace") as handle:
            text = handle.read(MAX_PREVIEW_BYTES)
        truncated = size > MAX_PREVIEW_BYTES
    except OSError as exc:
        log.write(f"[{RED}]Could not read: {escape(str(exc))}[/]")
        return

    if path.suffix.lower() == ".json" and not truncated:
        try:
            from rich.json import JSON as RichJSON

            log.write(RichJSON(text, indent=2))
            return
        except Exception:
            pass  # not valid JSON — fall through to plain text
    if truncated:
        log.write(f"[{YELLOW}]… this file is large; showing the first part …[/]")
    for line in text.splitlines():
        log.write(colorize(line))


class FileBrowserScreen(NavScreen):
    """Browse decrypted backup data; JSON files open in a pretty viewer."""

    BINDINGS = [Binding("escape", "back", "Back", priority=True)]

    def __init__(self) -> None:
        super().__init__()
        self._roots = browse_roots()

    def compose(self) -> ComposeResult:
        yield TitleBar("Browse backed-up data")
        if not self._roots:
            yield Static(
                f"\n[{YELLOW}]There is nothing to browse yet.[/]\n\n"
                f"[{WHITE}]The data inside a backup is locked. To look at it, "
                f"first restore a backup (menu → Restore files, or My backups "
                f"→ Restore from this backup). Then come back here to read "
                f"it — JSON files open in a friendly viewer.[/]",
                id="browser-empty",
            )
        else:
            options = [(label, index) for index, (label, _) in enumerate(self._roots)]
            yield Select(
                options, value=0, allow_blank=False, id="browser-root"
            )
            with Horizontal(id="browser-split"):
                yield DirectoryTree(str(self._roots[0][1]), id="browser-tree")
                yield RichLog(id="browser-preview", markup=True, wrap=True,
                              auto_scroll=False)
        with Horizontal(classes="button-row"):
            yield Button("← Back", id="browser-back", variant="primary")
        yield hint_bar("↑ ↓ move · Enter opens a folder or file · ← → reach the buttons · Esc back")

    def on_mount(self) -> None:
        if self._roots:
            preview = self.query_one("#browser-preview", RichLog)
            preview.write(
                f"[{MUTED}]Pick a file on the left to read it here.\n"
                f"JSON files are shown neatly formatted and colored.[/]"
            )
            self.query_one("#browser-tree", DirectoryTree).focus()

    @on(Select.Changed, "#browser-root")
    def _root_changed(self, event: Select.Changed) -> None:
        if event.value is Select.BLANK:
            return
        tree = self.query_one("#browser-tree", DirectoryTree)
        tree.path = str(self._roots[int(event.value)][1])
        tree.focus()

    @on(DirectoryTree.FileSelected, "#browser-tree")
    def _file_selected(self, event: DirectoryTree.FileSelected) -> None:
        path = Path(event.path)
        if path.suffix.lower() == ".zip":
            self.app.push_screen(ZipBrowserScreen(path))
            return
        preview_file(path, self.query_one("#browser-preview", RichLog))

    @on(Button.Pressed, "#browser-back")
    def _back_btn(self) -> None:
        self.app.pop_screen()

    def action_back(self) -> None:
        self.app.pop_screen()


class ZipBrowserScreen(NavScreen):
    """Open a zip (e.g. a Notion export) and read the files inside it."""

    BINDINGS = [Binding("escape", "back", "Back", priority=True)]

    def __init__(self, zip_path: Path, display_name: str | None = None,
                 cleanup: bool = False) -> None:
        super().__init__()
        self._zip_path = zip_path
        self._display_name = display_name or zip_path.name
        self._cleanup = cleanup
        self._members: list = []

    def on_unmount(self) -> None:
        if self._cleanup:
            try:
                self._zip_path.unlink(missing_ok=True)
            except OSError:
                pass

    def compose(self) -> ComposeResult:
        yield TitleBar(f"Inside {self._display_name}")
        with Horizontal(id="zip-split"):
            yield NavDataTable(id="zip-table")
            yield RichLog(id="zip-preview", markup=True, wrap=True, auto_scroll=False)
        with Horizontal(classes="button-row"):
            yield Button("← Back", id="zip-back", variant="primary")
        yield hint_bar("↑ ↓ choose a file · Enter reads it · ← → reach the buttons · Esc back")

    def on_mount(self) -> None:
        import zipfile

        table = self.query_one("#zip-table", DataTable)
        table.cursor_type = "row"
        table.add_columns("File", "Size")
        preview = self.query_one("#zip-preview", RichLog)
        try:
            with zipfile.ZipFile(self._zip_path) as zf:
                self._members = [m for m in zf.infolist() if not m.is_dir()]
        except (OSError, zipfile.BadZipFile) as exc:
            preview.write(f"[{RED}]Could not open this zip: {escape(str(exc))}[/]")
            return
        for index, member in enumerate(self._members):
            table.add_row(
                member.filename, human_size(member.file_size), key=str(index)
            )
        preview.write(
            f"[{MUTED}]{len(self._members)} files in this zip.\n"
            f"Pick one on the left to read it here — JSON is shown "
            f"neatly formatted.[/]"
        )
        table.focus()

    def _show_member(self, index: int) -> None:
        import zipfile

        preview = self.query_one("#zip-preview", RichLog)
        preview.clear()
        if not (0 <= index < len(self._members)):
            return
        member = self._members[index]
        suffix_early = Path(member.filename).suffix.lower()

        # A zip inside the zip (Notion exports do this): open it too.
        if suffix_early == ".zip":
            import tempfile

            try:
                with zipfile.ZipFile(self._zip_path) as zf:
                    with zf.open(member) as handle:
                        tmp = tempfile.NamedTemporaryFile(
                            suffix=".zip", delete=False
                        )
                        shutil.copyfileobj(handle, tmp)
                        tmp.close()
            except (OSError, zipfile.BadZipFile) as exc:
                preview.write(f"[{RED}]Could not open: {escape(str(exc))}[/]")
                return
            self.app.push_screen(
                ZipBrowserScreen(
                    Path(tmp.name),
                    display_name=Path(member.filename).name,
                    cleanup=True,
                )
            )
            return

        if suffix_early in (".png", ".jpg", ".jpeg", ".gif", ".pdf", ".ico"):
            preview.write(
                f"[{YELLOW}]{escape(member.filename)} is an image or binary "
                f"file ({human_size(member.file_size)}) — it cannot be shown "
                f"as text.[/]"
            )
            return

        try:
            with zipfile.ZipFile(self._zip_path) as zf:
                with zf.open(member) as handle:
                    data = handle.read(MAX_PREVIEW_BYTES + 1)
        except (OSError, zipfile.BadZipFile) as exc:
            preview.write(f"[{RED}]Could not read this file: {escape(str(exc))}[/]")
            return
        truncated = len(data) > MAX_PREVIEW_BYTES
        text = data[:MAX_PREVIEW_BYTES].decode("utf-8", errors="replace")
        suffix = Path(member.filename).suffix.lower()
        if suffix == ".json" and not truncated:
            try:
                from rich.json import JSON as RichJSON

                preview.write(RichJSON(text, indent=2))
                return
            except Exception:
                pass
        if truncated:
            preview.write(f"[{YELLOW}]… this file is large; showing the first part …[/]")
        for line in text.splitlines():
            preview.write(colorize(line))

    @on(DataTable.RowSelected, "#zip-table")
    def _row_selected(self, event: DataTable.RowSelected) -> None:
        if event.row_key is not None:
            self._show_member(int(str(event.row_key.value)))

    @on(Button.Pressed, "#zip-back")
    def _back_btn(self) -> None:
        self.app.pop_screen()

    def action_back(self) -> None:
        self.app.pop_screen()


# ======================================================================
# Scheduling
# ======================================================================
class ScheduleScreen(NavScreen):
    """Services, how often they run, unattended running, and retention."""

    BINDINGS = [Binding("escape", "back", "Back", priority=True)]

    def compose(self) -> ComposeResult:
        from orchestrator import cron as cron_mod

        cfg = read_kv_file(CONF_PATH)
        cron_status = cron_mod.status()
        yield TitleBar("Scheduling")
        with VerticalScroll(id="schedule-body"):
            yield Static(
                f"\n[b {CYAN}]Run by itself, without anyone logged in[/]\n"
                f"[{MUTED}]Lets this computer make backups on its own, at the "
                f"times you choose.[/]\n"
            )
            with Horizontal(classes="schedule-row"):
                yield Switch(
                    value=cron_status.installed
                    or cfg.get("CRON_ENABLED", "false").lower() == "true",
                    id="sched-cron-on",
                )
                yield Static(
                    f"[b {WHITE}]Automatic backups[/]\n"
                    f"[{MUTED}]{cron_status.summary}[/]",
                    classes="schedule-name",
                    id="cron-status-text",
                )
            with Horizontal(classes="form-row"):
                yield Label("Time zone", classes="form-label")
                yield Input(
                    value=cfg.get("CRON_TIMEZONE", "") or str(cron_mod.system_zone()),
                    placeholder="Asia/Kolkata",
                    id="sched-cron-zone",
                )

            yield Static(
                f"\n[b {CYAN}]What gets backed up, and when[/]\n"
                f"[{MUTED}]Each service keeps its own times. Add as many as you "
                f"need — press + for another.[/]\n"
            )
            last_run = {row["enable_key"]: row["last"] for row in schedule_rows()}
            for name, label, enable_key, times_key in cron_mod.SERVICES:
                # One card per service. Every height here is explicit: an
                # auto-height container inside a scroller collapses to its
                # first child, which put the Add button under the next
                # service and pushed the last one off the screen.
                with Vertical(classes="service-card"):
                    with Horizontal(classes="service-head"):
                        yield Switch(
                            value=cfg.get(enable_key, "false").lower() == "true",
                            id=f"sched-on-{enable_key}",
                        )
                        yield Static(
                            f"[b {WHITE}]{label}[/]\n"
                            f"[{MUTED}]last backed up: "
                            f"{last_run.get(enable_key, 'never')}[/]",
                            classes="service-name",
                        )
                        yield Static(
                            "", id=f"summary-{name}", classes="times-summary"
                        )
                    with Horizontal(classes="times-strip"):
                        yield Static("Runs at", classes="times-label")
                        with Horizontal(id=f"times-{name}", classes="times-block"):
                            for index, (hour, minute) in enumerate(
                                _times_for(cfg, times_key)
                            ):
                                yield time_row(
                                    name, index, f"{hour:02d}:{minute:02d}"
                                )
                        yield Button(
                            "+", id=f"addtime-{name}", classes="add-time",
                        )

            yield Static("", id="sched-cron-preview")

            yield Static(
                f"\n[b {CYAN}]How long backups are kept[/]\n"
                f"[{MUTED}]Each place keeps its own history — a short one here "
                f"to save disk space, longer ones elsewhere. "
                f"Type a number of days, 0, or the word forever.[/]\n"
            )
            for label, key, note in [
                ("On this computer", "LOCAL_RETENTION_DAYS",
                 "0 = delete as soon as it reaches the cloud or drive"),
                ("On Backblaze (cloud)", "BACKBLAZE_RETENTION_DAYS",
                 "730 deletes on day 731; 0 or forever = never delete"),
                ("On the USB / external drive", "USB_RETENTION_DAYS",
                 "applied when the drive is connected; 0 = never delete"),
            ]:
                with Horizontal(classes="schedule-row"):
                    yield Static(
                        f"[b {WHITE}]{label}[/]\n[{MUTED}]{note}[/]",
                        classes="schedule-name",
                    )
                    yield Static("keep", classes="schedule-every")
                    yield Input(
                        value=cfg.get(key, "forever"),
                        placeholder="14",
                        id=f"sched-keep-{key}",
                        classes="schedule-hours",
                    )
                    yield Static("days", classes="schedule-every")
            yield Static(
                f"\n[{MUTED}]Tidying happens at the end of each backup run. "
                f"The newest backup is never deleted, whatever these "
                f"say.[/]\n"
            )
        with Horizontal(classes="button-row"):
            yield Button("Save changes", id="schedule-save", variant="success")
            yield Button("Clean up now", id="schedule-prune")
            yield Button("← Back", id="schedule-back", variant="primary")
        yield hint_bar("↑ ↓ move between fields · Space flips a switch · Enter presses a button · Esc back")

    def on_mount(self) -> None:
        self._refresh_summaries()

    @on(Button.Pressed, ".add-time")
    async def _add_time(self, event: Button.Pressed) -> None:
        """Give this service another time box."""
        service = str(event.button.id).replace("addtime-", "")
        block = self.query_one(f"#times-{service}", Horizontal)
        existing = block.query(f".t-{service}")
        if len(existing) >= 12:
            self.notify("Twelve times a day is plenty.", title="Scheduling",
                        severity="warning")
            event.stop()
            return
        # Start the new box an hour after the last, as a reasonable guess.
        suggestion = "13:00"
        if existing:
            try:
                from orchestrator import cron as cron_mod
                hour, minute = cron_mod.parse_times(existing.last(Input).value)[0]
                suggestion = f"{(hour + 1) % 24:02d}:{minute:02d}"
            except (ValueError, IndexError):
                pass
        row = time_row(service, len(existing), suggestion)
        # Await the mount: until it completes the new Input is not in the DOM
        # yet, so neither focusing it nor counting it would find anything.
        await block.mount(row)
        row.query_one(Input).focus()
        self._refresh_summaries()
        event.stop()

    @on(Button.Pressed, ".remove-time")
    def _remove_time(self, event: Button.Pressed) -> None:
        """Take a time away, but never leave a service with none."""
        service = str(event.button.id).split("-")[1]
        block = self.query_one(f"#times-{service}", Horizontal)
        if len(block.query(".time-cell")) <= 1:
            self.notify(
                "A service needs at least one time. Switch it off instead.",
                title="Scheduling", severity="warning",
            )
            event.stop()
            return
        event.button.parent.remove()
        self.call_after_refresh(self._refresh_summaries)
        event.stop()

    @on(Input.Changed, ".time-input")
    @on(Input.Changed, "#sched-cron-zone")
    def _time_changed(self) -> None:
        self._refresh_summaries()

    def _collect_times(self) -> dict[str, str]:
        """{service: 'HH:MM,HH:MM'} straight from the boxes on screen."""
        from orchestrator import cron as cron_mod

        collected = {}
        for name, _, _, _ in cron_mod.SERVICES:
            values = [
                box.value.strip()
                for box in self.query(f".t-{name}").results(Input)
                if box.value.strip()
            ]
            collected[name] = ",".join(values)
        return collected

    def _refresh_summaries(self) -> None:
        """Say in words how often each service runs, and when next."""
        from orchestrator import cron as cron_mod

        zone_text = self.query_one("#sched-cron-zone", Input).value.strip()
        times = self._collect_times()

        for name, label, enable_key, _ in cron_mod.SERVICES:
            try:
                summary = self.query_one(f"#summary-{name}", Static)
            except Exception:
                continue
            try:
                parsed = cron_mod.parse_times(times.get(name, ""))
            except ValueError as exc:
                summary.update(f"[{RED}]{exc}[/]")
                continue
            count = len(parsed)
            word = "once a day" if count == 1 else f"{count} times a day"
            summary.update(f"[{MUTED}]{word}[/]")

        preview = self.query_one("#sched-cron-preview", Static)
        if not cron_mod.valid_zone(zone_text):
            preview.update(f"\n[{RED}]'{zone_text}' is not a time zone. Try "
                           f"something like Asia/Kolkata.[/]\n")
            return

        enabled_now = {
            name: times.get(name, "")
            for name, _, enable_key, _ in cron_mod.SERVICES
            if self.query_one(f"#sched-on-{enable_key}", Switch).value
            and times.get(name)
        }
        if not enabled_now:
            preview.update(f"\n[{MUTED}]Nothing is switched on.[/]\n")
            return

        try:
            merged = cron_mod.merge_schedule(enabled_now, zone_text)
        except ValueError:
            preview.update("")
            return

        lines = [f"\n[b {CYAN}]The next few runs[/]"]
        upcoming = cron_mod.next_runs(
            ",".join(enabled_now.values()), zone_text, count=4
        )
        for moment in upcoming:
            at = moment.split("· ")[-1]
            due = next((s for e, s, w in merged if w == at), [])
            names = ", ".join(
                label for n, label, _, _ in cron_mod.SERVICES if n in due
            )
            lines.append(f"[{WHITE}]{moment}[/]  [{MUTED}]{names}[/]")
        machine = cron_mod.system_zone()
        chosen = cron_mod.zone_name(zone_text)
        if str(machine) != chosen:
            lines.append(
                f"[{MUTED}]Times are {chosen}. This machine runs on {machine}, "
                f"so they are converted before they reach cron.[/]"
            )
        if cron_mod.observes_dst(zone_text):
            lines.append(
                f"[{YELLOW}]{chosen} changes its clocks during the year — "
                f"re-save this screen after a change so the times stay right.[/]"
            )
        preview.update("\n".join(lines) + "\n")

    @on(Switch.Changed, "#sched-cron-on")
    def _cron_toggled(self) -> None:
        self._refresh_summaries()

    @on(Button.Pressed, "#schedule-back")
    def _back_btn(self) -> None:
        self.app.pop_screen()

    def action_back(self) -> None:
        self.app.pop_screen()

    @on(Button.Pressed, "#schedule-save")
    def _save(self) -> None:
        from orchestrator import cron as cron_mod

        updates: dict[str, str] = {}
        times = self._collect_times()

        zone_text = self.query_one("#sched-cron-zone", Input).value.strip()
        if not cron_mod.valid_zone(zone_text):
            self.notify(
                f"'{zone_text}' is not a time zone. Try Asia/Kolkata.",
                title="Scheduling", severity="error",
            )
            return
        updates["CRON_TIMEZONE"] = zone_text

        # Check every box before writing anything, so a typo in the last one
        # cannot leave half the schedule saved.
        for name, label, enable_key, times_key in cron_mod.SERVICES:
            switch_on = self.query_one(f"#sched-on-{enable_key}", Switch).value
            updates[enable_key] = "true" if switch_on else "false"
            try:
                parsed = cron_mod.parse_times(times.get(name, ""))
            except ValueError as exc:
                if switch_on:
                    self.notify(f"{label}: {exc}", title="Scheduling",
                                severity="error")
                    return
                continue
            updates[times_key] = cron_mod.format_times(parsed).replace(" ", "")

        cron_on = self.query_one("#sched-cron-on", Switch).value
        updates["CRON_ENABLED"] = "true" if cron_on else "false"

        for key in ("LOCAL_RETENTION_DAYS", "BACKBLAZE_RETENTION_DAYS",
                    "USB_RETENTION_DAYS"):
            box = self.query_one(f"#sched-keep-{key}", Input)
            days = box.value.strip()
            if not days:
                continue
            if days.isdigit() or days.lower() in ("forever", "never"):
                updates[key] = days.lower() if not days.isdigit() else days
            else:
                self.notify(
                    f"'{days}' is not a number of days. Type a number, 0, "
                    f"or the word forever.",
                    title="Scheduling", severity="error",
                )
                box.focus()
                return

        try:
            save_conf_values(CONF_PATH, updates)
        except OSError as exc:
            self.notify(f"Could not save: {exc}", title="Scheduling", severity="error")
            return

        # Make the machine's cron match what was just saved.
        if cron_on:
            wanted = {
                name: times[name]
                for name, _, enable_key, _ in cron_mod.SERVICES
                if updates.get(enable_key) == "true" and times.get(name)
            }
            error = cron_mod.install(wanted, zone_text)
            runs = len(cron_mod.merge_schedule(wanted, zone_text)) if not error else 0
        else:
            error = cron_mod.remove()

        if error:
            self.notify(
                f"Settings saved, but automatic runs could not be set up: {error}",
                title="Scheduling", severity="error",
            )
            return
        if cron_on:
            self.notify(
                f"Saved. Backups will run at {runs} "
                f"time{'s' if runs != 1 else ''} a day, "
                f"even when nobody is logged in.",
                title="Scheduling",
            )
        else:
            self.notify("Saved. Automatic backups are off.", title="Scheduling")
        self.app.pop_screen()

    @on(Button.Pressed, "#schedule-prune")
    def _prune(self) -> None:
        self.app.push_screen(
            ConfirmScreen(
                "Clean up old backups now?",
                f"[{WHITE}]This removes backups that are older than the "
                f"limits above, in each place separately.\n\n"
                f"The newest backup is always kept, and nothing is removed "
                f"from this computer unless a copy exists somewhere else.[/]",
                yes_label="Yes, clean up",
            ),
            self._prune_confirmed,
        )

    def _prune_confirmed(self, confirmed: bool | None) -> None:
        if confirmed:
            self.notify("Cleaning up — this may take a moment…", title="Retention")
            self._prune_worker()

    @work(thread=True, exclusive=True, group="prune")
    def _prune_worker(self) -> None:
        try:
            from orchestrator.retention import apply_retention

            results = apply_retention()
            lines = [outcome.summary for outcome in results]
            removed = sum(len(outcome.deleted) for outcome in results)
        except Exception as exc:  # noqa: BLE001 — surfaced to the user
            lines, removed = [f"Clean-up failed: {exc}"], 0

        def render() -> None:
            self.notify(
                "\n".join(lines)
                + (f"\n\n{removed} old backups removed." if removed else
                   "\n\nNothing needed removing."),
                title="Clean-up finished",
                timeout=12,
            )

        self.app.call_from_thread(render)


# ======================================================================
# Logs
# ======================================================================
class LogsScreen(NavScreen):
    """Pick a past backup run and read its log."""

    BINDINGS = [Binding("escape", "back", "Back", priority=True)]

    def __init__(self) -> None:
        super().__init__()
        self._paths: list[Path] = []

    def compose(self) -> ComposeResult:
        yield TitleBar("Logs")
        yield Static(
            f"\n[{WHITE}]Every backup run writes a log. Pick one to read it.[/]\n",
            id="logs-intro",
        )
        yield NavDataTable(id="logs-table")
        with Horizontal(classes="button-row"):
            yield Button("Open selected log", id="logs-open", variant="primary")
            yield Button("← Back", id="logs-back")
        yield hint_bar("↑ ↓ choose a log · Enter opens it · ← → reach the buttons · Esc back")

    def on_mount(self) -> None:
        table = self.query_one("#logs-table", DataTable)
        table.cursor_type = "row"
        table.add_columns("Log", "When", "Size")
        logs = list_run_logs()
        self._paths = [path for path, _, _ in logs]
        for index, (path, moment, size) in enumerate(logs):
            table.add_row(
                log_display_name(path),
                moment.strftime("%d %b %Y · %H:%M"),
                human_size(size),
                key=str(index),
            )
        if not logs:
            self.query_one("#logs-intro", Static).update(
                f"\n[{YELLOW}]No logs found yet — run a backup first.[/]\n"
            )
            self.query_one("#logs-open", Button).disabled = True
        table.focus()

    def _open_row(self, index: int) -> None:
        if 0 <= index < len(self._paths):
            self.app.push_screen(LogViewScreen(self._paths[index]))

    @on(DataTable.RowSelected, "#logs-table")
    def _row_selected(self, event: DataTable.RowSelected) -> None:
        if event.row_key is not None:
            self._open_row(int(str(event.row_key.value)))

    @on(Button.Pressed, "#logs-open")
    def _open_btn(self) -> None:
        table = self.query_one("#logs-table", DataTable)
        if table.row_count:
            self._open_row(table.cursor_row)

    @on(Button.Pressed, "#logs-back")
    def _back_btn(self) -> None:
        self.app.pop_screen()

    def action_back(self) -> None:
        self.app.pop_screen()


class LogViewScreen(NavScreen):
    """Read one log file, colorized and scrollable."""

    BINDINGS = [Binding("escape", "back", "Back", priority=True)]

    MAX_BYTES = 2_000_000  # read at most the last 2 MB of very large logs

    def __init__(self, path: Path) -> None:
        super().__init__()
        self._path = path

    def compose(self) -> ComposeResult:
        yield TitleBar(log_display_name(self._path))
        yield Static(
            f"[{MUTED}]{escape(str(self._path.relative_to(PROJECT_ROOT)))}[/]",
            classes="log-path",
        )
        yield RichLog(id="log-view", markup=True, wrap=True, auto_scroll=False)
        with Horizontal(classes="button-row"):
            yield Button("← Back to log list", id="logview-back", variant="primary")
        yield hint_bar("↑ ↓ PgUp PgDn scroll · ← → reach the buttons · Esc goes back")

    def on_mount(self) -> None:
        log = self.query_one("#log-view", RichLog)
        if self._path.suffix.lower() == ".json":
            preview_file(self._path, log)
            log.focus()
            return
        try:
            size = self._path.stat().st_size
            with open(self._path, "r", errors="replace") as handle:
                if size > self.MAX_BYTES:
                    handle.seek(size - self.MAX_BYTES)
                    handle.readline()  # drop the partial first line
                    log.write(f"[{YELLOW}]… showing the end of a large log …[/]")
                for line in handle:
                    log.write(colorize(line.rstrip("\n")))
        except OSError as exc:
            log.write(f"[{RED}]Could not read this log: {escape(str(exc))}[/]")
        log.focus()

    @on(Button.Pressed, "#logview-back")
    def _back_btn(self) -> None:
        self.app.pop_screen()

    def action_back(self) -> None:
        self.app.pop_screen()


# ======================================================================
# API keys and passwords (credential store)
# ======================================================================
def credential_db():
    """(path, password) for the credential database, from .env."""
    env = read_kv_file(ENV_PATH)
    path = env.get("KEEPASS_DATABASE") or os.environ.get("KEEPASS_DATABASE", "")
    password = env.get("KEEPASS_PASSWORD") or os.environ.get(
        "KEEPASS_PASSWORD", ""
    )
    return path, password


def _kp_run(args, stdin_lines, timeout=30):
    """Run keepassxc-cli, feeding it the passwords it asks for on stdin."""
    return subprocess.run(
        ["keepassxc-cli", *args],
        input="".join(line + "\n" for line in stdin_lines),
        text=True,
        capture_output=True,
        timeout=timeout,
    )


def list_credentials():
    """Entry names in the database. Returns (names, error)."""
    path, password = credential_db()
    if not path or not password:
        return [], "No credential database is configured — run first-time setup."
    if shutil.which("keepassxc-cli") is None:
        return [], "keepassxc-cli is not installed on this machine."
    try:
        result = _kp_run(["ls", "--quiet", "--flatten", path], [password])
    except (subprocess.TimeoutExpired, OSError) as exc:
        return [], f"Could not open the database: {exc}"
    if result.returncode != 0:
        return [], (result.stderr.strip() or "Could not open the database.")
    names = [
        line.strip() for line in result.stdout.splitlines()
        if line.strip() and not line.strip().endswith("/")
    ]
    return names, None


def read_credential(name):
    """Current value of one entry. Returns (value, error)."""
    path, password = credential_db()
    try:
        result = _kp_run(
            ["show", "--attributes=password", "--show-protected", "--quiet",
             path, name],
            [password],
        )
    except (subprocess.TimeoutExpired, OSError) as exc:
        return None, str(exc)
    if result.returncode != 0:
        return None, (result.stderr.strip() or "Entry not found.")
    return result.stdout.strip(), None


def write_credential(name, value, create):
    """Set an entry's value. Returns error message, or None on success."""
    path, password = credential_db()
    if not path or not password:
        return "No credential database is configured."
    command = "add" if create else "edit"
    try:
        result = _kp_run(
            [command, path, name, "-p", "--quiet"],
            [password, value, value],
        )
    except (subprocess.TimeoutExpired, OSError) as exc:
        return str(exc)
    if result.returncode != 0:
        return result.stderr.strip() or f"keepassxc-cli {command} failed."
    return None


class CredentialEditScreen(NavModal):
    """Set the value of one credential."""

    BINDINGS = [Binding("escape", "close", "Close", priority=True)]

    def __init__(self, name, exists):
        super().__init__()
        self._name = name
        self._exists = exists

    def compose(self) -> ComposeResult:
        with Vertical(id="cred-dialog", classes="dialog"):
            yield Static(
                f"[b]{'CHANGE' if self._exists else 'ADD'}[/b] · {escape(self._name)}",
                classes="dialog-title",
            )
            yield Static(
                f"\n[{WHITE}]Type or paste the new value. It is written straight "
                f"into the credential database and never stored anywhere else.[/]\n"
            )
            yield Label(f"[{CYAN}]New value[/]")
            yield Input(password=True, id="cred-value")
            yield Label(f"[{CYAN}]Type it again[/]")
            yield Input(password=True, id="cred-confirm")
            yield Static("", id="cred-message")
            with Horizontal(classes="dialog-buttons"):
                yield Button("Save", id="cred-save", variant="success")
                yield Button("Show value", id="cred-reveal")
                yield Button("Cancel", id="cred-cancel", variant="error")

    def on_mount(self) -> None:
        self.query_one("#cred-value", Input).focus()

    @on(Button.Pressed, "#cred-reveal")
    def _reveal(self) -> None:
        for widget_id in ("#cred-value", "#cred-confirm"):
            field = self.query_one(widget_id, Input)
            field.password = not field.password
        button = self.query_one("#cred-reveal", Button)
        showing = not self.query_one("#cred-value", Input).password
        button.label = "Hide value" if showing else "Show value"

    @on(Button.Pressed, "#cred-cancel")
    def _cancel(self) -> None:
        self.dismiss(False)

    def action_close(self) -> None:
        self.dismiss(False)

    @on(Button.Pressed, "#cred-save")
    def _save(self) -> None:
        value = self.query_one("#cred-value", Input).value
        confirm = self.query_one("#cred-confirm", Input).value
        message = self.query_one("#cred-message", Static)

        if not value:
            message.update(f"[{YELLOW}]Enter a value first.[/]")
            return
        if value != confirm:
            message.update(f"[{RED}]The two values do not match.[/]")
            return

        message.update(f"[{CYAN}]Saving…[/]")
        error = write_credential(self._name, value, create=not self._exists)
        if error:
            message.update(f"[{RED}]{escape(error)}[/]")
            return

        self.notify(f"{self._name} saved.", title="Credentials")
        self.dismiss(True)


class CredentialAddScreen(NavModal):
    """Ask for the name of a new credential."""

    BINDINGS = [Binding("escape", "close", "Close", priority=True)]

    def compose(self) -> ComposeResult:
        with Vertical(id="cred-add-dialog", classes="dialog"):
            yield Static("[b]ADD A CREDENTIAL[/b]", classes="dialog-title")
            yield Static(
                f"\n[{WHITE}]The name must match exactly what the backup looks "
                f"for — for example [b]NOTION_TOKEN[/b].[/]\n"
            )
            yield Label(f"[{CYAN}]Name[/]")
            yield Input(id="cred-name", placeholder="e.g. CLOUDFLARE_API_TOKEN")
            with Horizontal(classes="dialog-buttons"):
                yield Button("Continue", id="cred-add-ok", variant="primary")
                yield Button("Cancel", id="cred-add-cancel", variant="error")

    def on_mount(self) -> None:
        self.query_one("#cred-name", Input).focus()

    @on(Button.Pressed, "#cred-add-ok")
    def _ok(self) -> None:
        name = self.query_one("#cred-name", Input).value.strip()
        if name:
            self.dismiss(name)

    @on(Button.Pressed, "#cred-add-cancel")
    def _cancel(self) -> None:
        self.dismiss(None)

    def action_close(self) -> None:
        self.dismiss(None)


class CredentialsScreen(NavScreen):
    """See which credentials are stored, and change them."""

    BINDINGS = [Binding("escape", "back", "Back", priority=True)]

    def compose(self) -> ComposeResult:
        yield TitleBar("API keys & passwords")
        yield Static("", id="cred-intro")
        yield NavDataTable(id="cred-table")
        with Horizontal(classes="button-row"):
            yield Button("Change selected", id="cred-change", variant="primary")
            yield Button("Add new", id="cred-new", variant="success")
            yield Button("← Back", id="cred-back")
        yield hint_bar("↑ ↓ choose · Enter changes it · ← → reach the buttons · Esc back")

    def on_mount(self) -> None:
        table = self.query_one("#cred-table", DataTable)
        table.cursor_type = "row"
        table.add_columns("Credential", "Used for", "Stored")
        self._reload()

    def _reload(self) -> None:
        table = self.query_one("#cred-table", DataTable)
        table.clear()

        names, error = list_credentials()
        intro = self.query_one("#cred-intro", Static)

        if error:
            intro.update(f"\n[{RED}]{escape(error)}[/]\n")
            self.query_one("#cred-change", Button).disabled = True
            self.query_one("#cred-new", Button).disabled = True
            return

        stored = set(names)
        missing = [n for n in REQUIRED_ENTRIES if n not in stored]

        intro.update(
            f"\n[{WHITE}]{len(stored)} credential"
            f"{'' if len(stored) == 1 else 's'} stored. "
            + (
                f"[{YELLOW}]{len(missing)} expected entries are missing.[/]"
                if missing else f"[{GREEN}]All expected entries present.[/]"
            )
            + f"[/]\n[{MUTED}]Values are never shown here — only whether they "
              f"exist. Changing one takes effect on the next backup.[/]\n"
        )

        # Expected entries first, then anything extra the database holds.
        for name in REQUIRED_ENTRIES:
            table.add_row(
                name,
                CREDENTIAL_PURPOSE.get(name, ""),
                "yes" if name in stored else "— missing —",
                key=name,
            )
        for name in sorted(stored - set(REQUIRED_ENTRIES)):
            table.add_row(name, "(extra entry)", "yes", key=name)

    def _selected_name(self):
        table = self.query_one("#cred-table", DataTable)
        if not table.row_count:
            return None
        row_key, _ = table.coordinate_to_cell_key((table.cursor_row, 0))
        return str(row_key.value) if row_key is not None else None

    def _edit(self, name):
        names, _ = list_credentials()
        self.app.push_screen(
            CredentialEditScreen(name, exists=name in names),
            lambda saved: self._reload() if saved else None,
        )

    @on(DataTable.RowSelected, "#cred-table")
    def _row_selected(self, event: DataTable.RowSelected) -> None:
        if event.row_key is not None:
            self._edit(str(event.row_key.value))

    @on(Button.Pressed, "#cred-change")
    def _change(self) -> None:
        name = self._selected_name()
        if name:
            self._edit(name)

    @on(Button.Pressed, "#cred-new")
    def _new(self) -> None:
        self.app.push_screen(
            CredentialAddScreen(),
            lambda name: self._edit(name) if name else None,
        )

    @on(Button.Pressed, "#cred-back")
    def _back_btn(self) -> None:
        self.app.pop_screen()

    def action_back(self) -> None:
        self.app.pop_screen()


# ======================================================================
# Encryption keys
# ======================================================================
class KeysScreen(NavScreen):
    """View the current keys, or generate a fresh pair."""

    BINDINGS = [Binding("escape", "back", "Back", priority=True)]

    def compose(self) -> ComposeResult:
        yield TitleBar("Encryption keys")
        with VerticalScroll(id="keys-body"):
            yield Static("", id="keys-info")
        with Horizontal(classes="button-row"):
            yield Button("Replace with new keys…", id="keys-rotate", variant="error")
            yield Button("← Back", id="keys-back", variant="primary")
        yield hint_bar("↑ ↓ ← → move between buttons · Enter presses · Esc goes back")

    def on_mount(self) -> None:
        self._refresh()

    def _refresh(self) -> None:
        public = PUBLIC_KEY.read_text().strip() if PUBLIC_KEY.exists() else None
        private_ok = PRIVATE_KEY.exists()
        backups = sorted(KEYS_DIR.glob("archive.key.bak-*"), reverse=True)

        def mark(ok: bool) -> str:
            return f"[{GREEN}]✓[/]" if ok else f"[{RED}]✗[/]"

        backup_lines = (
            "\n".join(
                f"    [{MUTED}]{b.name}  (from {datetime.fromtimestamp(b.stat().st_mtime).strftime('%d %b %Y %H:%M')})[/]"
                for b in backups[:5]
            )
            or f"    [{MUTED}]none[/]"
        )
        self.query_one("#keys-info", Static).update(
            f"""
[{WHITE}]Backups are locked with a key pair: a [b]lock[/b] (public key) that
seals every backup, and a [b]key[/b] (private key) that opens them again.[/]

  {mark(PUBLIC_KEY.exists())} [{WHITE}]Lock (public key)[/]   [{MUTED}]{PUBLIC_KEY.relative_to(PROJECT_ROOT)}[/]
      [{CYAN}]{escape(public) if public else '— missing —'}[/]

  {mark(private_ok)} [{WHITE}]Key (private key)[/]   [{MUTED}]{PRIVATE_KEY.relative_to(PROJECT_ROOT)}[/]
      [{MUTED}]{'present — keep this file safe; it is needed to restore' if private_ok else '— missing —'}[/]

[{WHITE}]Saved copies of older keys:[/]
{backup_lines}

[{YELLOW}]Important:[/] [{WHITE}]backups made with an older key can only be opened
with that older key. Replacing keys keeps a safe copy of the old
ones right next to the new ones.[/]
"""
        )

    @on(Button.Pressed, "#keys-back")
    def _back_btn(self) -> None:
        self.app.pop_screen()

    def action_back(self) -> None:
        self.app.pop_screen()

    @on(Button.Pressed, "#keys-rotate")
    def _rotate_pressed(self) -> None:
        self.app.push_screen(
            ConfirmScreen(
                "Replace the encryption keys?",
                f"[{WHITE}]New backups will use the new keys from now on.\n\n"
                f"Backups you already have will still need today's key —\n"
                f"a copy of it is kept safely in the same folder.\n\n"
                f"Do you want to continue?[/]",
                yes_label="Yes, make new keys",
            ),
            self._rotate_confirmed,
        )

    def _rotate_confirmed(self, confirmed: bool | None) -> None:
        if confirmed:
            self._rotate()

    def _rotate(self) -> None:
        if shutil.which("age-keygen") is None:
            self.notify("age-keygen is not installed on this machine.",
                        title="Keys", severity="error")
            return
        stamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
        try:
            KEYS_DIR.mkdir(parents=True, exist_ok=True)
            if PRIVATE_KEY.exists():
                shutil.copy2(PRIVATE_KEY, KEYS_DIR / f"archive.key.bak-{stamp}")
                os.chmod(KEYS_DIR / f"archive.key.bak-{stamp}", 0o600)
            if PUBLIC_KEY.exists():
                shutil.copy2(PUBLIC_KEY, KEYS_DIR / f"archive.pub.bak-{stamp}")

            generated = subprocess.run(
                ["age-keygen"], capture_output=True, text=True, timeout=30, check=True,
            )
            PRIVATE_KEY.write_text(generated.stdout)
            os.chmod(PRIVATE_KEY, 0o600)

            derived = subprocess.run(
                ["age-keygen", "-y", str(PRIVATE_KEY)],
                capture_output=True, text=True, timeout=30, check=True,
            )
            PUBLIC_KEY.write_text(derived.stdout)
        except (OSError, subprocess.SubprocessError) as exc:
            self.notify(f"Key replacement failed: {exc}", title="Keys", severity="error")
            return
        self._refresh()
        self.notify("New keys are in place. A copy of the old ones was saved.",
                    title="Keys")


# ======================================================================
# Settings
# ======================================================================
class SettingField:
    """One editable setting: "input", "switch", "number" or "choice".

    Anything but a plain input is checked before saving, so a bad value
    fails here rather than somewhere unrelated much later.
    """

    def __init__(self, key: str, label: str, kind: str = "input",
                 choices: tuple[str, ...] = (), minimum: int | None = None,
                 maximum: int | None = None) -> None:
        self.key, self.label, self.kind = key, label, kind
        self.choices, self.minimum, self.maximum = choices, minimum, maximum

    def problem(self, value: str) -> str | None:
        """What is wrong with this value, in words, or None if it is fine."""
        value = value.strip()
        if not value:
            return None
        if self.kind == "number":
            if not value.lstrip("-").isdigit():
                return f"{self.label}: '{value}' is not a whole number"
            number = int(value)
            if self.minimum is not None and number < self.minimum:
                return f"{self.label}: must be {self.minimum} or more"
            if self.maximum is not None and number > self.maximum:
                return f"{self.label}: must be {self.maximum} or less"
        if self.kind == "choice" and value.upper() not in self.choices:
            return (f"{self.label}: pick one of "
                    f"{', '.join(self.choices)}")
        return None


SETTING_SECTIONS: list[tuple[str, list[SettingField]]] = [
    ("Where backups are kept", [
        SettingField("REPOSITORY_ENABLED", "Keep backups on this computer", "switch"),
        SettingField("REPOSITORY_PATH", "Folder for backups"),
        SettingField("BACKBLAZE_ENABLED", "Also copy backups to the cloud (B2)", "switch"),
        SettingField("BACKBLAZE_REMOTE", "Cloud remote name"),
        SettingField("BACKBLAZE_BUCKET", "Cloud bucket name"),
        SettingField("USB_ENABLED", "Also copy backups to a USB drive", "switch"),
        SettingField("USB_LABEL", "USB drive label"),
        SettingField("USB_BACKUP_PATH", "Folder on the USB drive"),
    ]),
    ("Notifications", [
        SettingField("TELEGRAM_ENABLED", "Telegram messages", "switch"),
        SettingField("TELEGRAM_CHAT_ID", "Telegram chat ID"),
        SettingField("EMAIL_ENABLED", "Email alerts", "switch"),
        SettingField("EMAIL_SMTP_HOST", "Email server"),
        SettingField("EMAIL_SMTP_PORT", "Email server port", "number",
                     minimum=1, maximum=65535),
        SettingField("EMAIL_FROM", "Send alerts from"),
        SettingField("EMAIL_TO", "Send alerts to"),
        SettingField("SLACK_ENABLED", "Slack messages", "switch"),
        SettingField("TEAMS_ENABLED", "Microsoft Teams messages", "switch"),
        SettingField("PAGERDUTY_ENABLED", "PagerDuty alerts", "switch"),
    ]),
    ("Advanced", [
        SettingField("INCREMENTAL", "Space-saving backups (incremental)", "switch"),
        SettingField("WORKSPACE", "Working folder"),
        SettingField("RESTORE_DIR", "Default restore folder"),
        SettingField("LOG_LEVEL", "Log detail level", "choice",
                     choices=("QUIET", "NORMAL", "VERBOSE")),
        SettingField("AUDIT_RETENTION_DAYS", "Keep audit records for (days)",
                     "number", minimum=0),
        SettingField("NOTION_WORKSPACE_URL", "Notion workspace address"),
        SettingField("NOTION_EXPORT_TIMEOUT_MINUTES", "Notion export time limit (min)",
                     "number", minimum=1),
    ]),
]

ALL_SETTING_FIELDS = [field for _, fields in SETTING_SECTIONS for field in fields]


class SettingsScreen(NavScreen):
    """Friendly editor for backup.conf, plus the .env access fields."""

    BINDINGS = [Binding("escape", "back", "Back", priority=True)]

    def compose(self) -> ComposeResult:
        cfg = read_kv_file(CONF_PATH)
        env = read_kv_file(ENV_PATH)
        yield TitleBar("Settings")
        with TabbedContent(id="settings-tabs"):
            with TabPane("Options", id="tab-conf"):
                with VerticalScroll(id="conf-scroll"):
                    for section, fields in SETTING_SECTIONS:
                        yield Static(f"[b {CYAN}]{section}[/]", classes="conf-section")
                        for field in fields:
                            with Horizontal(classes="form-row"):
                                yield Label(field.label, classes="form-label")
                                if field.kind == "switch":
                                    yield Switch(
                                        value=cfg.get(field.key, "false").lower() == "true",
                                        id=f"conf-{field.key}",
                                    )
                                else:
                                    yield Input(
                                        value=cfg.get(field.key, ""),
                                        id=f"conf-{field.key}",
                                    )
            with TabPane("Access", id="tab-env"):
                with VerticalScroll(id="env-scroll"):
                    yield Static("")
                    with Horizontal(classes="form-row"):
                        yield Label("Database file", classes="form-label")
                        yield Input(value=env.get("KEEPASS_DATABASE", ""), id="env-db")
                    with Horizontal(classes="form-row"):
                        yield Label("Password", classes="form-label")
                        yield Input(
                            value=env.get("KEEPASS_PASSWORD", ""),
                            password=True,
                            id="env-pass",
                        )
        with Horizontal(classes="button-row"):
            yield Button("Save changes", id="settings-save", variant="success")
            yield Button("← Back", id="settings-back", variant="primary")
        yield hint_bar("↑ ↓ move between fields · Space flips a switch · Enter presses a button · Esc back")

    @on(Button.Pressed, "#settings-back")
    def _back_btn(self) -> None:
        self.app.pop_screen()

    def action_back(self) -> None:
        self.app.pop_screen()

    @on(Button.Pressed, "#settings-save")
    def _save(self) -> None:
        updates: dict[str, str] = {}
        for field in ALL_SETTING_FIELDS:
            widget = self.query_one(f"#conf-{field.key}")
            if isinstance(widget, Switch):
                updates[field.key] = "true" if widget.value else "false"
            elif isinstance(widget, Input):
                value = widget.value.strip()
                problem = field.problem(value)
                if problem:
                    self.notify(problem, title="Settings", severity="error")
                    widget.focus()
                    return
                if value:
                    updates[field.key] = value
        try:
            save_conf_values(CONF_PATH, updates)
        except OSError as exc:
            self.notify(f"Could not save settings: {exc}",
                        title="Settings", severity="error")
            return

        db = self.query_one("#env-db", Input).value.strip()
        password = self.query_one("#env-pass", Input).value
        if db and password:
            try:
                write_env_file(db, password)
            except OSError as exc:
                self.notify(f"Options saved, but access details failed: {exc}",
                            title="Settings", severity="error")
                return
        self.notify("Settings saved.", title="Settings")
        self.app.pop_screen()


# ======================================================================
# Setup wizard (modal, 6 steps)
# ======================================================================
class SetupWizardScreen(NavModal):
    BINDINGS = [Binding("escape", "close", "Close", priority=True)]

    STEP_TITLES = [
        "Welcome",
        "Access details",
        "Your settings",
        "Required entries",
        "Check-up",
        "All done",
    ]
    STEP_IDS = ["step-welcome", "step-env", "step-conf", "step-secrets",
                "step-test", "step-done"]

    step = reactive(0)

    def compose(self) -> ComposeResult:
        env = read_kv_file(ENV_PATH)
        entries_list = "\n".join(
            f"  [{CYAN}]•[/] [{WHITE}]{name}[/]" for name in REQUIRED_ENTRIES
        )

        with Vertical(id="wizard-dialog", classes="dialog"):
            yield Static("", id="wizard-title", classes="dialog-title")
            with ContentSwitcher(initial="step-welcome", id="wizard-switcher"):
                with VerticalScroll(id="step-welcome", classes="wizard-step"):
                    yield Static(
                        f"""
[b {WHITE}]Welcome to HonestBackup.[/]

This short walk-through gets everything ready:

  [{CYAN}]1[/]  Enter the access details
  [{CYAN}]2[/]  Look over your settings
  [{CYAN}]3[/]  Check the required entries
  [{CYAN}]4[/]  Run a quick check-up
  [{CYAN}]5[/]  Finish — and make your first backup

Press [b {CYAN}]Next[/] to begin.
"""
                    )
                with VerticalScroll(id="step-env", classes="wizard-step"):
                    status = (
                        f"[{GREEN}]✓ Access details already saved — you can leave "
                        f"this step as it is.[/]"
                        if ENV_PATH.exists()
                        else f"[{YELLOW}]Please fill in both fields below.[/]"
                    )
                    yield Static(status + "\n")
                    yield Label(f"[{CYAN}]Database file[/]  [{MUTED}](file path)[/]")
                    yield Input(
                        value=env.get("KEEPASS_DATABASE", str(PROJECT_ROOT / "secrets.kdbx")),
                        id="wiz-env-db",
                    )
                    yield Label(f"[{CYAN}]Password[/]")
                    yield Input(
                        value=env.get("KEEPASS_PASSWORD", ""),
                        password=True,
                        id="wiz-env-pass",
                    )
                    yield Static(f"\n[{MUTED}]Saved automatically when you press Next.[/]")
                with VerticalScroll(id="step-conf", classes="wizard-step"):
                    yield Static(
                        f"[{WHITE}]Choose what should be backed up. You can change "
                        f"this any time from [b]Settings[/b] or [b]Scheduling[/b].[/]\n"
                    )
                    cfg = read_kv_file(CONF_PATH)
                    for label, enable_key, _ in SERVICES:
                        with Horizontal(classes="form-row"):
                            yield Label(label, classes="form-label")
                            yield Switch(
                                value=cfg.get(enable_key, "false").lower() == "true",
                                id=f"wiz-svc-{enable_key}",
                            )
                    yield Static("")
                    with Horizontal(classes="form-row"):
                        yield Label("Folder where backups are kept", classes="form-label")
                        yield Input(
                            value=cfg.get("REPOSITORY_PATH", "./backupvault"),
                            id="wiz-repo-path",
                        )
                    yield Static(f"\n[{MUTED}]Saved automatically when you press Next.[/]")
                with VerticalScroll(id="step-secrets", classes="wizard-step"):
                    yield Static(
                        f"""
[{WHITE}]The following entries must be available at runtime.
Each entry [b]title[/b] must match the name exactly, with the value
stored in the entry's [b]password[/b] field.[/]

{entries_list}
"""
                    )
                with VerticalScroll(id="step-test", classes="wizard-step"):
                    yield Static(
                        f"[{WHITE}]Press the button to make sure everything is in "
                        f"place. This checks your details, the programs this "
                        f"computer needs, your settings and your keys.[/]\n"
                    )
                    yield Button("Run the check-up", id="wiz-test-run", variant="primary")
                    yield RichLog(id="wiz-test-log", markup=True, wrap=True)
                with VerticalScroll(id="step-done", classes="wizard-step"):
                    yield Static(
                        f"""
[b {GREEN}]All done![/]

[{WHITE}]You are ready to go. From the main menu:[/]

  [{CYAN}]•[/]  Choose [b]Back up now[/b] to make your first backup
  [{CYAN}]•[/]  Choose [b]Scheduling[/b] to decide how often backups happen
  [{CYAN}]•[/]  Choose [b]View logs[/b] afterwards to see how it went

[b {WHITE}]Thank you for using HonestBackup.[/]
"""
                    )
            with Horizontal(classes="dialog-buttons"):
                yield Button("← Back", id="wiz-back")
                yield Button("Next →", id="wiz-next", variant="primary")
                yield Button("Close", id="wiz-cancel", variant="error")

    def on_mount(self) -> None:
        self._refresh_chrome()

    def watch_step(self, _: int) -> None:
        if self.is_mounted:
            self._refresh_chrome()

    def _refresh_chrome(self) -> None:
        total = len(self.STEP_IDS)
        dots = "".join(
            f"[{CYAN}]●[/]" if i == self.step else f"[{MUTED}]○[/]" for i in range(total)
        )
        self.query_one("#wizard-title", Static).update(
            f"[b]FIRST-TIME SETUP[/b] · {self.STEP_TITLES[self.step]}   "
            f"{dots}  [{MUTED}]step {self.step + 1} of {total}[/]"
        )
        self.query_one("#wizard-switcher", ContentSwitcher).current = self.STEP_IDS[self.step]
        self.query_one("#wiz-back", Button).disabled = self.step == 0
        nxt = self.query_one("#wiz-next", Button)
        nxt.label = "Finish ✓" if self.step == total - 1 else "Next →"

    @on(Button.Pressed, "#wiz-back")
    def _back(self) -> None:
        if self.step > 0:
            self.step -= 1

    @on(Button.Pressed, "#wiz-next")
    def _next(self) -> None:
        if self.step == 1 and not self._save_env_step():
            return
        if self.step == 2:
            self._save_conf_step()
        if self.step >= len(self.STEP_IDS) - 1:
            self.dismiss(None)
        else:
            self.step += 1

    @on(Button.Pressed, "#wiz-cancel")
    def _cancel(self) -> None:
        self.dismiss(None)

    def action_close(self) -> None:
        self.dismiss(None)

    def _save_env_step(self) -> bool:
        db = self.query_one("#wiz-env-db", Input).value.strip()
        password = self.query_one("#wiz-env-pass", Input).value
        if not db or not password:
            if ENV_PATH.exists():
                return True
            self.notify("Please fill in both fields.", title="Setup", severity="warning")
            return False
        try:
            write_env_file(db, password)
        except OSError as exc:
            self.notify(f"Could not save: {exc}", title="Setup", severity="error")
            return False
        self.notify("Access details saved.", title="Setup")
        return True

    def _save_conf_step(self) -> None:
        updates: dict[str, str] = {}
        for _, enable_key, _, _ in SERVICES:
            switch = self.query_one(f"#wiz-svc-{enable_key}", Switch)
            updates[enable_key] = "true" if switch.value else "false"
        repo = self.query_one("#wiz-repo-path", Input).value.strip()
        if repo:
            updates["REPOSITORY_PATH"] = repo
        try:
            save_conf_values(CONF_PATH, updates)
        except OSError as exc:
            self.notify(f"Could not save: {exc}", title="Setup", severity="error")

    @on(Button.Pressed, "#wiz-test-run")
    def _run_test(self) -> None:
        log = self.query_one("#wiz-test-log", RichLog)
        log.clear()
        log.write(f"[{CYAN}]Checking…[/]")
        self._test_worker()

    @work(thread=True, exclusive=True, group="wizard-test")
    def _test_worker(self) -> None:
        results: list[tuple[bool, str]] = []
        env = read_kv_file(ENV_PATH)
        db = env.get("KEEPASS_DATABASE") or os.environ.get("KEEPASS_DATABASE", "")
        password = env.get("KEEPASS_PASSWORD") or os.environ.get("KEEPASS_PASSWORD", "")

        results.append((ENV_PATH.exists(), "access details are saved"))
        results.append((bool(db), "database file is set"))
        results.append((bool(password), "password is set"))
        if db:
            results.append((Path(db).expanduser().exists(), "database file exists"))

        for binary, why in [
            ("keepassxc-cli", "credential access"),
            ("age", "locks the backups"),
            ("zstd", "shrinks the backups"),
            ("tar", "packs the backups"),
            ("rclone", "copies backups to the cloud"),
        ]:
            results.append(
                (shutil.which(binary) is not None, f"{binary} is installed ({why})")
            )

        results.append((CONF_PATH.exists(), "settings file is present"))
        results.append((PRIVATE_KEY.exists(), "private key is present"))
        results.append((PUBLIC_KEY.exists(), "public key is present"))

        cfg = read_kv_file(CONF_PATH)
        repo = PROJECT_ROOT / cfg.get("REPOSITORY_PATH", "./backupvault")
        results.append(
            (repo.exists() and os.access(repo, os.W_OK), "backup folder is usable")
        )

        if db and password and shutil.which("keepassxc-cli") and Path(db).expanduser().exists():
            try:
                proc = subprocess.run(
                    ["keepassxc-cli", "ls", "--quiet", db],
                    input=password, text=True, capture_output=True, timeout=20,
                )
                results.append((proc.returncode == 0, "database opens with the saved password"))
            except (subprocess.TimeoutExpired, OSError):
                results.append((False, "database opens (check timed out)"))

        def render() -> None:
            log = self.query_one("#wiz-test-log", RichLog)
            passed = sum(1 for ok, _ in results if ok)
            for ok, label in results:
                mark = f"[{GREEN}]✓[/]" if ok else f"[{RED}]✗[/]"
                log.write(f" {mark} [{WHITE}]{escape(label)}[/]")
            if passed == len(results):
                log.write(f"\n[b {GREEN}]Everything looks good — {passed} of {len(results)} checks passed.[/]")
            else:
                log.write(
                    f"\n[b {YELLOW}]{passed} of {len(results)} checks passed. "
                    f"The ✗ lines above show what needs attention.[/]"
                )

        self.app.call_from_thread(render)


# ======================================================================
# Restore wizard (modal, 3 steps)
# ======================================================================
class RestoreWizardScreen(NavModal):
    BINDINGS = [Binding("escape", "close", "Close", priority=True)]

    step = reactive(0)
    STEP_IDS = ["restore-pick", "restore-options", "restore-confirm"]
    STEP_TITLES = ["Choose a backup", "Choose where it goes", "Confirm"]

    def __init__(self, preselected: str | None = None) -> None:
        super().__init__()
        self.selected: str | None = None
        self._preselected = preselected

    def compose(self) -> ComposeResult:
        with Vertical(id="restore-dialog", classes="dialog"):
            yield Static("", id="restore-title", classes="dialog-title")
            with ContentSwitcher(initial="restore-pick", id="restore-switcher"):
                with Vertical(id="restore-pick", classes="wizard-step"):
                    yield Static(
                        f"[{WHITE}]Pick the backup you want to bring files back from.\n"
                        f"[{MUTED}]The newest one is at the top.[/][/]\n"
                    )
                    yield NavDataTable(id="restore-table")
                with VerticalScroll(id="restore-options", classes="wizard-step"):
                    yield Label(f"[{CYAN}]Folder to put the restored files in[/]")
                    yield Input(value="./restore", id="restore-dir")
                    yield Label(
                        f"[{CYAN}]Key file[/]  "
                        f"[{MUTED}](leave as it is unless you were told otherwise)[/]"
                    )
                    yield Input(
                        value=str(PRIVATE_KEY.relative_to(PROJECT_ROOT)),
                        id="restore-key",
                    )
                    yield Label(
                        f"[{CYAN}]Only these files[/]  "
                        f"[{MUTED}](optional — leave empty to bring everything back)[/]"
                    )
                    yield TextArea("", id="restore-files")
                with VerticalScroll(id="restore-confirm", classes="wizard-step"):
                    yield Static("", id="restore-summary")
            with Horizontal(classes="dialog-buttons"):
                yield Button("← Back", id="restore-back")
                yield Button("Next →", id="restore-next", variant="primary")
                yield Button("Cancel", id="restore-cancel", variant="error")

    def on_mount(self) -> None:
        table = self.query_one("#restore-table", DataTable)
        table.cursor_type = "row"
        table.add_columns("Backup", "Size")
        backups = list_local_backups()
        if not backups:
            self.notify("There are no backups yet.", title="Restore", severity="warning")
        for backup_id, stamp, size in backups:
            table.add_row(stamp, size, key=backup_id)
        if backups:
            self.selected = backups[0][0]
        if self._preselected and any(b[0] == self._preselected for b in backups):
            self.selected = self._preselected
            index = next(i for i, b in enumerate(backups) if b[0] == self._preselected)
            table.move_cursor(row=index)
            self.step = 1  # skip straight to "where should it go"
        table.focus()
        self._refresh_chrome()

    def watch_step(self, _: int) -> None:
        if self.is_mounted:
            self._refresh_chrome()

    def _refresh_chrome(self) -> None:
        total = len(self.STEP_IDS)
        dots = "".join(
            f"[{CYAN}]●[/]" if i == self.step else f"[{MUTED}]○[/]" for i in range(total)
        )
        self.query_one("#restore-title", Static).update(
            f"[b]RESTORE[/b] · {self.STEP_TITLES[self.step]}   {dots}"
        )
        self.query_one("#restore-switcher", ContentSwitcher).current = self.STEP_IDS[self.step]
        self.query_one("#restore-back", Button).disabled = self.step == 0
        nxt = self.query_one("#restore-next", Button)
        nxt.label = "Start restoring ▶" if self.step == total - 1 else "Next →"
        nxt.disabled = self.step == 0 and self.selected is None
        if self.step == total - 1:
            self._render_summary()

    @on(DataTable.RowHighlighted)
    def _row_highlighted(self, event: DataTable.RowHighlighted) -> None:
        if event.row_key is not None:
            self.selected = str(event.row_key.value)
            self.query_one("#restore-next", Button).disabled = False

    @on(DataTable.RowSelected)
    def _row_selected(self, event: DataTable.RowSelected) -> None:
        self.selected = str(event.row_key.value)
        self.step = 1

    def _gather(self) -> dict:
        files_text = self.query_one("#restore-files", TextArea).text
        return {
            "backup_id": self.selected,
            "restore_dir": self.query_one("#restore-dir", Input).value.strip() or "./restore",
            "private_key": self.query_one("#restore-key", Input).value.strip()
            or str(PRIVATE_KEY),
            "files": [f.strip() for f in files_text.splitlines() if f.strip()],
        }

    def _render_summary(self) -> None:
        plan = self._gather()
        moment = backup_id_to_datetime(str(plan["backup_id"]))
        friendly = moment.strftime("%d %b %Y at %H:%M") if moment else str(plan["backup_id"])
        files = (
            "\n".join(f"    [{WHITE}]{escape(f)}[/]" for f in plan["files"])
            if plan["files"]
            else f"    [{WHITE}]everything in the backup[/]"
        )
        self.query_one("#restore-summary", Static).update(
            f"""
[{WHITE}]Here is what will happen:[/]

  [{CYAN}]From backup[/]   [{WHITE}]{friendly}[/]
  [{CYAN}]Files go to[/]   [{WHITE}]{escape(plan['restore_dir'])}[/]
  [{CYAN}]Bringing back[/]
{files}

[{MUTED}]Nothing is deleted — files are copied out of the backup into
the folder above. You can watch the progress live.[/]
"""
        )

    @on(Button.Pressed, "#restore-back")
    def _back(self) -> None:
        if self.step > 0:
            self.step -= 1

    @on(Button.Pressed, "#restore-next")
    def _next(self) -> None:
        if self.step == 0 and self.selected is None:
            self.notify("Pick a backup first.", title="Restore", severity="warning")
            return
        if self.step < len(self.STEP_IDS) - 1:
            self.step += 1
        else:
            self.dismiss(self._gather())

    @on(Button.Pressed, "#restore-cancel")
    def _cancel(self) -> None:
        self.dismiss(None)

    def action_close(self) -> None:
        self.dismiss(None)


# ======================================================================
# Help
# ======================================================================
class HelpScreen(NavModal):
    BINDINGS = [Binding("escape", "close", "Close", priority=True)]

    def compose(self) -> ComposeResult:
        with Vertical(id="help-dialog", classes="dialog"):
            yield Static("[b]HELP[/b]", classes="dialog-title")
            yield Static(
                f"""
[b {CYAN}]Getting around[/]

  [{CYAN}]↑ ↓[/]      Move through a list, or between fields and buttons
  [{CYAN}]← →[/]      Move between buttons and fields
  [{CYAN}]Enter[/]    Open what is selected, or press the selected button
  [{CYAN}]Space[/]    Flip a switch on or off
  [{CYAN}]1 – 9[/]    Jump straight to one of the first nine menu items
  [{CYAN}]Tab[/]      Move forward (same as ↓)
  [{CYAN}]Esc[/]      Back to the previous screen
  [{CYAN}]q[/]        Quit

[{MUTED}]In a list, ↑ ↓ moves through the list — press → or Tab to step out
to the buttons underneath.[/]

[b {CYAN}]The menu[/]

  [{WHITE}]Back up now[/]          Right away, or only what is due
  [{WHITE}]My backups[/]           Every saved backup. Check one against its
                        SHA-256, look inside it, or ask whether the
                        cloud still holds everything it should
  [{WHITE}]Restore files[/]        Bring files back out of a backup
  [{WHITE}]Logs & reports[/]       What happened during past runs
  [{WHITE}]Read backed-up data[/]  Browse collected or restored files
  [{WHITE}]Scheduling[/]           What runs, at which times, and how long
                        each copy is kept
  [{WHITE}]Settings[/]             Storage, notifications, log detail
  [{WHITE}]API keys & passwords[/] Add or change stored credentials
  [{WHITE}]Encryption keys[/]      View or replace the keys that lock backups
  [{WHITE}]First-time setup[/]     A guided walk-through

[b {CYAN}]Worth knowing[/]

  [{WHITE}]Backups are locked.[/]  The archives are encrypted. To read an old
  one, restore it first — then it appears under Read backed-up data.

  [{WHITE}]Times are yours.[/]  Each service has its own run times, written in
  the time zone you set. Services sharing a time share one run.

  [{WHITE}]Each copy is kept for its own length.[/]  On this computer, 0 means
  delete once it is safely in the cloud. On the cloud or the drive, 0 means
  never delete. Tidying happens at the end of each run.

  [{WHITE}]The newest backup is never deleted[/], whatever the settings say.

[{MUTED}]A backup keeps running if you leave its screen — "Show progress"
appears at the top of the menu while it does.[/]
""",
                classes="dialog-body",
            )
            with Horizontal(classes="dialog-buttons"):
                yield Button("Close", id="help-close", variant="primary")

    @on(Button.Pressed, "#help-close")
    def _close(self) -> None:
        self.dismiss(None)

    def action_close(self) -> None:
        self.dismiss(None)


# ======================================================================
# Application
# ======================================================================
class HonestbackupTUI(App):
    """HonestBackup — friendly menu-driven TUI, pure AMOLED black."""

    TITLE = "HONESTBACKUP"
    ENABLE_COMMAND_PALETTE = False

    busy = reactive(False)

    def __init__(self) -> None:
        super().__init__()
        self.job_name: str | None = None
        self.output_lines: list[str] = []
        self._proc: subprocess.Popen | None = None

    # ------------------------------------------------------------------
    def on_mount(self) -> None:
        self.push_screen(HomeScreen())

    # -- job control -----------------------------------------------------
    def backup_mode_chosen(self, mode: str | None) -> None:
        if mode is None:
            return
        argv = [sys.executable, "-u", "-m", "orchestrator.run"]
        if mode == "force":
            argv.append("--force")
        elif mode == "incremental":
            argv += ["--force", "--incremental"]
        # "due" adds no flags — the orchestrator follows the schedule.
        self.start_job("Backing up", argv)

    def restore_confirmed(self, plan: dict | None) -> None:
        if not plan:
            return
        argv = [
            sys.executable, "-u", "-m", "orchestrator.run",
            "--restore",
            "--backup-id", plan["backup_id"],
            "--private-key", plan["private_key"],
            "--restore-dir", plan["restore_dir"],
        ]
        if plan["files"]:
            argv += ["--files", *plan["files"]]
        self.start_job("Restoring", argv)

    def start_job(self, name: str, argv: list[str]) -> None:
        if self.busy:
            self.notify("Something is already running — showing its progress.",
                        title="HonestBackup")
            if not isinstance(self.screen, ActivityScreen):
                self.push_screen(ActivityScreen())
            return
        self.job_name = name
        self.busy = True
        self.output_lines = [
            f"[b {CYAN}]▶ {escape(name)} started — "
            f"{datetime.now().strftime('%H:%M:%S')}[/]",
            "",
        ]
        self.push_screen(ActivityScreen())
        self._job_worker(name, argv)

    def cancel_job(self) -> None:
        if self._proc is not None and self._proc.poll() is None:
            self._proc.terminate()
            self._emit(f"[{YELLOW}]Stopping — please wait…[/]")
        else:
            self.notify("Nothing is running.", title="HonestBackup")

    @work(thread=True, exclusive=True, group="job")
    def _job_worker(self, name: str, argv: list[str]) -> None:
        env = os.environ.copy()
        for key, value in read_kv_file(ENV_PATH).items():
            env.setdefault(key, value)
        try:
            proc = subprocess.Popen(
                argv,
                cwd=PROJECT_ROOT,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1,
                env=env,
            )
        except OSError as exc:
            self.call_from_thread(self._job_finished, name, None, str(exc))
            return
        self._proc = proc
        assert proc.stdout is not None
        for line in proc.stdout:
            self.call_from_thread(self._emit, "  " + colorize(line.rstrip("\n")))
        code = proc.wait()
        self._proc = None
        self.call_from_thread(self._job_finished, name, code, None)

    def _emit(self, line: str) -> None:
        self.output_lines.append(line)
        if len(self.output_lines) > 5000:
            del self.output_lines[:1000]
        if isinstance(self.screen, ActivityScreen):
            self.screen.append(line)

    def _job_finished(self, name: str, code: int | None, error: str | None) -> None:
        self.busy = False
        self.job_name = None
        if error is not None:
            self._emit(f"[b {RED}]✗ {escape(name)} could not start: {escape(error)}[/]")
            self.notify(f"{name} could not start.", title="HonestBackup", severity="error")
        elif code == 0:
            self._emit(f"[b {GREEN}]✓ {escape(name)} finished successfully.[/]")
            self.notify(f"{name} finished successfully.", title="HonestBackup")
        else:
            self._emit(
                f"[b {RED}]✗ {escape(name)} stopped with a problem (code {code}). "
                f"Check the lines above.[/]"
            )
            self.notify(f"{name} ran into a problem.", title="HonestBackup",
                        severity="error")
        if isinstance(self.screen, ActivityScreen):
            self.screen.job_done()

    # ------------------------------------------------------------------
    CSS = f"""
    * {{
        scrollbar-background: {BLACK};
        scrollbar-background-hover: {BLACK};
        scrollbar-background-active: {BLACK};
        scrollbar-color: {BORDER};
        scrollbar-color-hover: {CYAN};
        scrollbar-color-active: {CYAN};
        scrollbar-size-vertical: 1;
    }}

    Screen {{
        background: {BLACK};
        color: {WHITE};
    }}

    .title-bar {{
        height: 2;
        padding: 0 2;
        background: {BLACK};
        border-bottom: solid {BORDER};
    }}
    .hint-bar {{
        height: 2;
        padding: 0 2;
        background: {BLACK};
        border-top: solid {BORDER};
    }}
    .button-row {{
        height: 3;
        align: center middle;
        background: {BLACK};
    }}

    /* ---------- home ---------- */
    #home-header {{
        height: 1;
        padding: 0 2;
    }}
    #home-brand {{ width: 1fr; }}
    #home-clock {{ width: auto; }}
    #home-status {{
        height: auto;
        padding: 0 3;
    }}
    #home-menu {{
        height: 1fr;
        margin: 0 2 1 2;
        background: {BLACK};
        border: none;
        padding: 0 1;
    }}
    #home-menu:focus {{ border: none; }}
    OptionList {{ background: {BLACK}; }}
    OptionList > .option-list--option {{ padding: 0 1 1 1; }}
    OptionList > .option-list--option-highlighted {{ background: #003a3a; }}

    /* ---------- activity ---------- */
    #activity-log {{
        height: 1fr;
        margin: 1 2;
        background: {BLACK};
        border: none;
    }}

    /* ---------- schedule ---------- */
    #schedule-body {{ height: 1fr; padding: 0 3; }}
    .schedule-row {{ height: 4; }}
    .schedule-name {{ width: 1fr; padding: 0 2; }}
    .schedule-every {{ width: auto; padding: 1 1 0 0; color: {MUTED}; }}

    /* One card per service: a header row, then a strip of run times.
       Every height is fixed — auto heights collapse inside the scroller. */
    .service-card {{
        height: 9;
        margin: 0 0 1 0;
        border: round {BORDER};
        background: $boost;
    }}
    .service-head {{ height: 4; }}
    .service-name {{ width: 1fr; padding: 1 2 0 2; }}
    .times-summary {{ width: 18; padding: 1 2 0 0; text-align: right; }}

    .times-strip {{ height: 3; padding: 0 1 0 2; }}
    .times-label {{ width: 10; padding: 1 0 0 0; color: {MUTED}; }}
    .times-block {{ width: 1fr; height: 3; overflow-x: auto; }}
    .time-cell {{ width: 15; height: 3; }}
    .time-input {{ width: 11; min-width: 11; }}
    .remove-time {{
        width: 3; min-width: 3; height: 3;
        border: none; background: transparent; color: {MUTED};
    }}
    .remove-time:hover {{ background: {RED}; color: {BLACK}; }}
    .add-time {{
        width: 5; min-width: 5; height: 3;
        border: none; background: {PANEL}; color: {CYAN};
    }}
    .add-time:hover {{ background: {CYAN}; color: {BLACK}; }}
    .schedule-hours {{ width: 11; }}

    /* ---------- logs ---------- */
    #logs-intro {{ height: auto; padding: 0 3; }}
    #logs-table {{ height: 1fr; margin: 0 2 1 2; }}
    .log-path {{ height: 1; padding: 0 2; }}
    #log-view {{ height: 1fr; margin: 1 2; background: {BLACK}; border: none; }}

    /* ---------- keys ---------- */
    #keys-body {{ height: 1fr; padding: 0 3; }}

    /* ---------- file browser ---------- */
    #browser-empty {{ height: 1fr; padding: 0 3; }}
    #browser-root {{ margin: 1 2 0 2; }}
    #contents-split {{ height: 1fr; margin: 0 2 1 2; }}
    #contents-tree {{
        width: 45%; min-width: 34;
        background: {PANEL}; border: round {BORDER};
        padding: 0 1; margin-right: 1;
    }}
    #contents-preview {{ width: 1fr; background: {BLACK}; padding: 0 1; }}
    #contents-summary {{ padding: 0 2; }}

    #browser-split {{ height: 1fr; margin: 0 2 1 2; }}
    #browser-tree {{
        width: 42%;
        background: {BLACK};
        border-right: solid {BORDER};
        padding: 0 1;
    }}
    #browser-preview {{ width: 1fr; background: {BLACK}; padding: 0 1; }}
    #zip-split {{ height: 1fr; margin: 1 2; }}
    #zip-table {{ width: 45%; }}
    #zip-preview {{ width: 1fr; background: {BLACK}; padding: 0 1; }}
    DirectoryTree {{ background: {BLACK}; color: {WHITE}; }}
    DirectoryTree > .directory-tree--folder {{ color: {CYAN}; text-style: bold; }}
    DirectoryTree > .directory-tree--file {{ color: {WHITE}; }}
    DirectoryTree > .directory-tree--extension {{ color: {MUTED}; }}
    Tree > .tree--cursor {{ background: #003a3a; }}
    Tree > .tree--guides {{ color: {BORDER}; }}
    Select {{ background: {PANEL}; }}
    Select > SelectCurrent {{ background: {PANEL}; border: tall {BORDER}; color: {WHITE}; }}
    Select:focus > SelectCurrent {{ border: tall {CYAN}; }}
    SelectOverlay {{ background: {PANEL}; border: round {BORDER}; color: {WHITE}; }}

    /* ---------- credentials ---------- */
    #cred-intro {{ height: auto; padding: 0 3; }}
    #cred-table {{ height: 1fr; margin: 0 2 1 2; }}
    #cred-dialog, #cred-add-dialog {{ height: auto; max-height: 90%; width: 76; }}
    #cred-message {{ height: auto; padding: 1 0 0 0; }}

    /* ---------- backups ---------- */
    #backups-intro {{ height: auto; padding: 0 3; }}
    #backups-table {{ height: 1fr; margin: 0 2 1 2; }}
    #backup-detail-body {{ height: 1fr; padding: 0 3; }}
    #contents-log {{ height: 1fr; margin: 1 2; background: {BLACK}; border: none; }}
    #backup-options-dialog {{ height: auto; max-height: 90%; width: 76; }}
    #backup-options-list {{ height: auto; }}
    #backup-options-list Button {{ width: 100%; margin: 0; }}
    .option-note {{ padding: 0 1 1 1; }}

    /* ---------- settings ---------- */
    #settings-tabs {{ height: 1fr; }}
    #conf-scroll, #env-scroll {{ padding: 0 2; }}
    .conf-section {{ margin: 1 0 0 0; }}
    .form-row {{ height: 3; }}
    .form-label {{
        width: 36;
        height: 3;
        content-align: left middle;
        color: {WHITE};
    }}
    .form-row Input {{ width: 1fr; }}

    /* ---------- shared widgets ---------- */
    Input {{
        background: {PANEL};
        color: {WHITE};
        border: tall {BORDER};
    }}
    Input:focus {{ border: tall {CYAN}; background: {PANEL}; }}
    Input .input--placeholder {{ color: {MUTED}; }}

    TextArea {{
        background: {PANEL};
        color: {WHITE};
        border: tall {BORDER};
        height: 12;
    }}
    TextArea:focus {{ border: tall {CYAN}; }}

    Button {{
        background: {BLACK};
        color: {WHITE};
        border: none;
        text-style: bold;
        margin: 0 1;
        min-width: 14;
    }}
    Button:hover {{ background: #001a1a; color: {CYAN}; }}
    /* The focus ring has to be unmistakable — it is the only thing telling a
       keyboard user where they are. */
    Button:focus {{
        text-style: bold reverse;
        color: {CYAN};
    }}
    DataTable:focus {{ border: tall {CYAN}; }}
    OptionList:focus {{ border: tall {CYAN}; }}
    Switch:focus {{ border: tall {CYAN}; }}
    Button.-primary {{ background: #002a2a; color: {CYAN}; }}
    Button.-primary:hover {{ background: #004040; }}
    Button.-success {{ background: #002a14; color: {GREEN}; }}
    Button.-success:hover {{ background: #004020; }}
    Button.-error {{ background: #2a000a; color: {RED}; }}
    Button.-error:hover {{ background: #400010; }}
    Button:disabled {{ color: {MUTED}; background: {BLACK}; }}

    DataTable {{
        background: {BLACK};
        color: {WHITE};
        height: auto;
        max-height: 100%;
    }}
    DataTable > .datatable--header {{
        background: {BLACK};
        color: {CYAN};
        text-style: bold;
    }}
    DataTable > .datatable--cursor {{ background: #003a3a; color: {WHITE}; }}
    DataTable > .datatable--hover {{ background: #001a1a; }}

    Switch {{ background: {PANEL}; border: none; }}
    Switch > .switch--slider {{ color: {MUTED}; background: {PANEL}; }}
    Switch.-on > .switch--slider {{ color: {CYAN}; }}

    RichLog {{ background: {BLACK}; color: {WHITE}; border: none; }}

    TabbedContent ContentTabs {{ background: {BLACK}; }}
    Tabs {{ background: {BLACK}; }}
    Tab {{ color: {MUTED}; }}
    Tab.-active {{ color: {CYAN}; text-style: bold; }}
    Underline > .underline--bar {{ color: {CYAN}; background: {BORDER}; }}

    /* ---------- modals ---------- */
    SetupWizardScreen, RestoreWizardScreen, HelpScreen, ConfirmScreen,
    BackupOptionsScreen, CredentialEditScreen, CredentialAddScreen {{
        align: center middle;
        background: {BLACK} 60%;
    }}
    .dialog {{
        background: {BLACK};
        border: round {CYAN};
        width: 90%;
        max-width: 110;
        height: 85%;
        padding: 1 2;
    }}
    #help-dialog {{ height: auto; max-height: 90%; }}
    #confirm-dialog {{ height: auto; max-height: 80%; width: 70; }}
    .dialog-title {{
        dock: top;
        height: 1;
        color: {WHITE};
        margin-bottom: 1;
    }}
    .dialog-body {{ height: auto; overflow-y: auto; }}
    .dialog-buttons {{
        dock: bottom;
        height: 3;
        align: center middle;
        padding-top: 1;
    }}
    .wizard-step {{ height: 1fr; padding: 0 1; }}
    #wizard-switcher, #restore-switcher {{ height: 1fr; }}
    #wiz-test-log {{ height: 1fr; min-height: 8; margin-top: 1; }}
    #wiz-conf-view {{ height: 16; }}
    #restore-table {{ height: 1fr; }}
    #restore-files {{ height: 8; }}
    """


# ----------------------------------------------------------------------
# Entrypoint
# ----------------------------------------------------------------------
def main() -> None:
    HonestbackupTUI().run()


if __name__ == "__main__":  # pragma: no cover
    main()
