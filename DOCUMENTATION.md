# HonestBackup - Detailed Design & Implementation Documentation

This document provides an in-depth explanation of the HonestBackup project, covering its architecture, design decisions, component interactions, and usage guidelines. It is intended for developers, maintainers, and contributors who wish to understand the system thoroughly.

## Table of Contents
1. [Project Overview](#project-overview)
2. [Core Architecture](#core-architecture)
3. [Data Flow & Pipeline](#data-flow--pipeline)
4. [Collectors](#collectors)
5. [Security Model](#security-model)
6. [Configuration](#configuration)
7. [Incremental Backup Mechanism](#incremental-backup-mechanism)
8. [Orchestrator Workflow](#orchestrator-workflow)
9. [Utilities & Helper Modules](#utilities--helper-modules)
10. [Text User Interface (TUI)](#text-user-interface-tui)
11. [Extending the System](#extending-the-system)
12. [Testing & Debugging](#testing--debugging)
13. [Deployment & Operations](#deployment--operations)
14. [Glossary](#glossary)

---

## Project Overview

HonestBackup is a **compliance‑first** backup framework designed to preserve organizational configuration, metadata, audit evidence, and documentation from multiple SaaS platforms (Microsoft 365, Cloudflare, Notion). Unlike traditional backup tools that prioritize disaster recovery, HonestBackup focuses on:

- **Compliance & Governance** – retaining immutable records for audits.
- **Digital Forensics & Incident Response** – providing tamper‑evident artifacts.
- **Configuration Auditing** – tracking changes over time.
- **Historical Record Keeping** – enabling long‑term trend analysis.

The system collects data via dedicated **collectors**, stores it in a timestamped workspace, builds a manifest, creates an archive, compresses, encrypts, verifies, and finally stores the artifact in a backup vault.

### Key Goals
- **Zero secret leakage**: No credentials are stored in configuration files or environment variables beyond the pointer to a KeePass database.
- **Modularity**: Each service (M365, Cloudflare, Notion) is isolated in its own collector.
- **Deterministic output**: Same input yields same archive (aside from timestamps) to facilitate verification.
- **Storage efficiency**: Optional incremental backups using hardlinks.
- **Operational simplicity**: Single command line entry point with optional TUI for interactive control.

---

## Core Architecture

The system follows a linear pipeline with clearly defined stages:

```
+----------------+    +--------------------+    +-----------+    +----------+    +----------+    +----------+    +-----------+
|   Collectors   | -> |   Workspace (YYYY-MM-DD) | -> |  Manifest | -> | Archive  | -> | Compress   | -> | Encrypt     | -> | Verify      | -> | Storage     |
+----------------+    +--------------------+    +-----------+    +----------+    +----------+    +----------+    +-----------+
```

Each stage is implemented as a separate function or class in the `orchestrator` package. The orchestrator orchestrates the sequence, handles errors, and generates reports.

### High‑Level Modules

| Module | Responsibility |
|--------|----------------|
| `collectors/` | Service‑specific data extraction (M365, Cloudflare, Notion). |
| `orchestrator/` | Main workflow: `run.py`, session handling, scheduling, reporting. |
| `storage/` | Repository abstraction for storing archives, hashes, manifests. |
| `lib/` | Cross‑cutting concerns: logging, secrets management, alerting, reporting. |
| `tui/` | Optional Text User Interface for interactive control. |
| `config/` | Static configuration (`backup.conf.example`). |
| `scripts/` | Helper scripts (`run_backup.sh`, `setup.sh`). |

---

## Data Flow & Pipeline

### 1. Initialization
- `orchestrator/run.py` parses command‑line arguments (`--force`, `--incremental`, `--tui`, etc.).
- Loads `backup.conf` via `lib.secrets.get_config()`.
- Loads environment variables (including `KEEPASS_DATABASE`/`KEEPASS_PASSWORD`) via `lib.secrets.load_env()`.
- If `--tui` is present, launches `tui/app.py` and exits.

### 2. Session & Backup ID
- Generates a unique backup ID (`<timestamp>-<random>`) via `orchestrator/id.new_backup_id()`.
- The date portion (`YYYY-MM-DD`) becomes the workspace directory name.

### 3. Workspace Preparation
- If `INCREMENTAL=true` (config flag or `--incremental`), the script attempts to hardlink the previous day’s workspace into the new one using `orchestrator/create_incremental_workspace()` (uses `cp -al` or `rsync --link-dest` fallback).
- Otherwise, creates a fresh directory.

### 4. Collector Execution
For each enabled service (M365, Cloudflare, Notion):
- Runs whichever collectors this invocation is for. Cron passes `--only m365,cloudflare`; with no `--only`, every enabled collector runs.
- Executes the collector function (`orchestrator/run_m365`, `run_cloudflare`, `run_notion`).
- The collector writes raw files into `<workspace>/<service>/...`.
- After collection, the orchestrator calls `process_collector_result()` to record success/failure, warnings, and errors in the `BackupReport`.

### 5. Manifest Generation
- `orchestrator/manifest.build_manifest()` walks the workspace, computes relative paths, sizes, and creates a JSON manifest listing every file with metadata.

### 6. Archiving
- `orchestrator/archive.build_archive()` creates a POSIX tar archive (`tar -cf`) of the workspace directory, preserving permissions.
- The archive is stored temporarily in the workspace as `<backup_id>.tar`.

### 7. Compression
- The tarball is compressed with **Zstandard** (`zstd -q -o <archive>.tar.zst <archive>.tar`), yielding `.tar.zst`.
- Chosen for excellent compression ratio and speed; alternative gzip/bzip2 were considered but Zstandard offers better throughput.

### 8. Encryption
- The compressed archive is encrypted using **age** (`age -r <recipient> -o <archive>.tar.zst.age <archive>.tar.zst`).
- Recipients are defined in the age recipients file (by default, a file containing the host’s public key). This ensures only holders of the private key can decrypt.
- Age was selected over GPG for simplicity, modern cryptography (X25519), and minimal dependencies.

### 9. Verification
- Computes SHA‑256 of the encrypted archive and stores it alongside the archive in `backupvault/hashes/`.
- The hash is also recorded in the manifest for integrity verification.

### 10. Storage
- The encrypted archive (`*.tar.zst.age`) is moved to `backupvault/archives/`.
- The SHA‑256 hash file goes to `backupvault/hashes/`.
- The manifest (JSON) goes to `backupvault/manifests/`.
- Optionally, the orchestrator can sync to external targets (USB, Backblaze B2, S3) via `storage/sync.py`.

### 11. Cleanup
- Temporary workspace files are removed (`orchestrator/cleanup_workspace()`), leaving only the archived artifacts.
- If incremental mode is used, the hardlink farm preserves unchanged files, minimizing storage growth.

---

## Collectors

Each collector lives in `orchestrator/` as a thin wrapper that calls the actual collection logic in `collectors/<service>/collector.py`. The wrapper handles logging, error reporting, and updating the `BackupReport`.

### Microsoft 365 Collector (`collectors/m365/collector.py`)
- Uses the **Microsoft Graph API** via the `msgraph` SDK (or direct REST calls with token obtained via client credentials flow).
- **Data Collected**:
  - Directory objects: users, groups, service principals, applications, roles.
  - Conditional Access policies, Named Locations.
  - Security: Secure Score, Risk Detections, Identity Protection.
  - Audit & Sign‑in logs (limited by retention policies; pulls latest available).
  - Domain information, organizational details.
- **Why**: These items represent the configuration and security posture of the tenant, essential for compliance audits and forensic reconstruction of access controls.

### Cloudflare Collector (`collectors/cloudflare/collector.py`)
- Uses the **Cloudflare API v4** with API token authentication.
- **Data Collected**:
  - DNS records (all types).
  - Zone settings (SSL/TLS, edge cache, etc.).
  - Rulesets (firewall, WAF, page rules, transforms).
  - DNSSEC status.
  - **Security**: Firewall rules, WAF packages, rate limiting rules, IP access rules.
  - **Zero Trust**: Access Applications, Policies, Groups, Service Tokens, Tunnel configs, Device Posture, Identity Providers, Gateway Rules.
  - **Audit**: Audit logs (Enterprise plan).
- **Why**: Cloudflare often sits at the network edge; its configuration dictates traffic filtering, DDoS protection, and zero‑trust accesscritical for security posture.

### Notion Collector (`collectors/notion/collector.py`)
- Operates in two modes: **Browser Export** (headless Chromium via Playwright) and **API Export** (official Notion API).
- **Browser Export**:
  - Launches a headless browser, logs into the workspace (using credentials from KeePass if needed, otherwise relies on existing session), and triggers **Export → Markdown & CSV** and **Export → HTML & CSV** for the entire workspace.
  - Outputs two ZIP files: `markdown_export.zip` and `html_export.zip` under `workspace/notion/browser/`.
- **API Export**:
  - Calls `/v1/workspace` to get workspace info.
  - Lists users (`/v1/users`), pages (`/v1/search` with filter `page`), databases, and their rows.
  - Retrieves block children for each page (recursively, with pagination) to capture full content.
  - Stores JSON artifacts under `workspace/notion/api/`.
- **Why**: Notion does not provide a single “backup” API; combining browser export (for full fidelity) with API export (for structured metadata) ensures both human‑readable and machine‑processable archives.

---

## Security Model

### Core Principle
> **No secret material (passwords, tokens, keys) shall ever be written to configuration files, environment variables beyond the KeePass pointer, or stored in plain text on disk.**

### Implementation Details

| Element | Purpose | How It Is Secured |
|---------|---------|-------------------|
| `KEEPASS_DATABASE` / `KEEPASS_PASSWORD` | Pointers to the KeePass database that holds all actual secrets. | Only these two variables are allowed in `.env` or exported in the shell. They are **not** secrets themselves (they point to a protected database). |
| `backup.conf` | Non‑runtime configuration (toggles, run times, paths). | Contains only boolean flags, paths and clock times. No secrets. |
| `lib/secrets.load_env()` | Loads secrets from KeePass into the process environment at runtime. | Uses `keepassxc-cli show --attributes=password --show-protected --quiet <db> "<entry_name>"`. The master password is supplied via stdin (from `KEEPASS_PASSWORD`). The returned secrets are injected into `os.environ` for the duration of the lifetime: Only for the duration of the orchestrator/TUI process; never written to disk. |
| `lib/secrets.get_config()` | Reads `backup.conf`. | No secrets are read here. |
| Audit logs / collected data | May contain sensitive data (e.g., email contents, PII). | This is **by design**: the backup’s purpose is to preserve such data for compliance. The protection comes from encrypting the final archive with age (only holders of the private key can decrypt) and storing the vault in a restricted location (e.g., encrypted disk, offline media). |

### Rationale for KeePass
- Widely adopted, cross‑platform, open‑source (KeePassXC).
- Strong encryption (AES‑256, PBES2, Argon2).
- CLI tool `keepassxc-cli` enables scripted, non‑interactive secret retrieval.
- Centralizing secrets reduces sprawl and simplifies rotation (update the entry in KeePass, no code changes).

### Why Not Environment Variables Directly?
Environment variables are exposed to child processes and can be leaked via logging or `/proc`. By keeping only the KEEPASS pointer in the environment, the attack surface is minimized.

### Why Not HashiCorp Vault / AWS Secrets Manager?
Those introduce external dependencies and network calls. KeePass works offline, fitting the “air‑gapable” ethos of the project.

---

## Configuration

### `backup.conf` (located in `config/`)
- **Format**: `KEY=VALUE` lines, comments start with `#`.
- **Keys** (non‑exhaustive):
  - `WORKSPACE`: Base directory for daily workspace (default `./workspace`).
  - `BACKUP_TARGET`: Where archives, hashes, manifests are stored (default `./backupvault`).
  - `ENABLE_M365`, `ENABLE_CLOUDFLARE`, `ENABLE_NOTION`: Boolean toggles.
  - `M365_TIMES`, `CLOUDFLARE_TIMES`, `NOTION_TIMES`: the clock times each collector runs at, e.g. `01:00,13:00`. Read in `CRON_TIMEZONE`.
  - `CRON_TIMEZONE`: the zone those times are written in, e.g. `Asia/Kolkata`. Converted to the machine's own zone before reaching cron.
  - `CRON_ENABLED`: whether the crontab entries are installed at all.
  - `INCREMENTAL`: Enables hardlink‑based incremental backups.
  - `REPOSITORY_ENABLED`: Whether to use the local repository (always true unless you disable for testing).
  - `BACKBLAZE_ENABLED`, `ENABLE_S3`, `ENABLE_USB` etc.: Flags for external storage sync.
  - Notification sections (`EMAIL_*`, `TELEGRAM_*`): Toggle and configure alerting.

### `.env` (optional, located in project root)
- **Only allowed keys**:
  - `KEEPASS_DATABASE`: Absolute or relative path to the `.kdbx` file.
  - `KEEPASS_PASSWORD`: Master password for the database.
- **Any other key** will be ignored by `load_env()` (a warning may be logged).
- **File permissions**: Should be `600` (readable only by the owning user) to prevent accidental exposure.

### Environment Variables (set at runtime)
- In addition to the two above, the orchestrator expects the following to be present **after** `load_env()` runs (they are pulled from KeePass):
  - `TENANT_ID`, `CLIENT_ID`, `CLIENT_SECRET`
  - `CLOUDFLARE_API_TOKEN`, `ZONE_ID`, `CLOUDFLARE_ACCOUNT_ID`
  - `NOTION_TOKEN`
  - `B2_KEY_ID`, `B2_APPLICATION_KEY`
  - `EMAIL_USERNAME`, `EMAIL_PASSWORD`
  - `WEBHOOK_HMAC_SECRET`
  - `SLACK_WEBHOOK_URL`, `TEAMS_WEBHOOK_URL`
  - `PAGERDUTY_INTEGRATION_KEY`
  - `TELEGRAM_BOT_TOKEN` (if Telegram alerts are enabled)

If any of these are missing, the script will raise a `RuntimeError` indicating which KeePass entry could not be found.

---

## Incremental Backup Mechanism

### Motivation
Storing a full copy of the workspace for each day quickly consumes storage, especially when most files (e.g., unchanged audit logs, configuration snapshots) remain identical between runs.

### Approach
- **Hardlink‑based incremental backup**, similar to Apple’s Time Machine or `rsync --link-dest`.
- When `INCREMENTAL=true` (or `--incremental` flag), the orchestrator:
  1. Looks for the most recent `YYYY-MM-DD` directory under `WORKSPACE`.
  2. If found, creates the new day’s directory by **hardlinking** every file from the previous day (`cp -al <prev>/* <new>/` or via `rsync -a --link-dest=<prev>/ <new>/`).
  3. Any file that the collector modifies or creates will **break the link** (a new inode is allocated), storing only the delta.
  4. Unchanged files continue to share the same inode, consuming no extra space.

### Requirements
- Filesystem must support hardlinks (ext4, XFS, Btrfs, APFS). Network filesystems like NFS v4 may support them but require proper mounting.
- The `WORKSPACE` and `BACKUP_TARGET` must reside on the **same filesystem** for hardlinks to work across directories (hardlinks cannot cross filesystem boundaries).
- Sufficient inodes: each file, even if hardlinked, consumes an inode; however, since hardlinks share inodes, the count does not increase for unchanged files.

### Advantages
- Near‑zero storage overhead for unchanged data.
- Simple to implement and verify (no complex deduplication database).
- Compatible with the existing tar/age/zstd pipeline (the archive step sees the exact same file content whether it’s a hardlink or a copy).

### Limitations
- If a file is modified, the **entire file** is stored anew (no block‑level deduplication).
- Not suitable for workloads with massive numbers of tiny files that change frequently (inode exhaustion). In such cases, a true deduplication backend (e.g., ZFS, btrfs send/receive) would be preferable, but that adds operational complexity.

### Usage
- Enable in `backup.conf`: `INCREMENTAL=true`
- Or run manually: `python3 -m orchestrator.run --incremental --force`

---

## Orchestrator Workflow (Deep Dive)

### `orchestrator/run.py` – `main()`
1. **Argument Parsing** (`argparse`):
   - `--only m365,cloudflare`: run just these collectors. Cron uses it so a service scheduled three times a day is not dragged along by one scheduled six times a day.
   - `--force`: run regardless of anything else.
   - `--incremental`: Override config to enable incremental mode.
   - `--tui`: Launch the Text User Interface and exit.
   - `--list`, `--backup-id`, `--restore-dir`, `--private-key`, `--files`: Restore‑mode arguments (see Restore section).
   - `--version`: Print version and exit.
2. **Load Configuration & Secrets**:
   - `get_config()` reads `backup.conf`.
   - `load_env()` populates environment with secrets from KeePass.
3. **Initialize Core Objects**:
   - `BackupSession` (metadata: ID, start time, collectors list, replication flags).
   - `Scheduler` (loads last run timestamps from a JSON file in `~/.honestbackup/scheduler.json`).
   - `Logger` (writes to `<workspace>/logs/backup_<ID>.log`ID`.log`).
   - `BackupReport` (collects statistics, warnings, errors).
4. **Workspace Setup**:
   - Determine today’s date string (`YYYY-MM-DD` from backup ID).
   - Call `create_incremental_workspace()` if needed.
5. **Collector Loop**:
   - For each service (`m365`, `cloudflare`, `notion`):
     - If the collector is enabled and named by `--only` (or `--only` was not given):
       - Log section start.
       - Call the appropriate `run_<service>` function.
       - On success: increment executed counter, mark scheduler complete, add collector to session.
       - On failure: log error, record in report, send alert (if configured).
     - Else: the collector is not in `--only`, so it is left for its own scheduled time.
6. **Post‑Collection**:
   - If zero collectors ran (`executed_collectors == 0`): mark session as skipped, write report, finish.
   - Else: proceed to manifest, archive, compress, encrypt, verify.
7. **Manifest**: `build_manifest(workspace, backup_id)` → returns manifest object (contains file list, total size, etc.).
8. **Archive**: `build_archive(backup_id, day, manifest)` → creates `<backup_id>.tar` in workspace.
9. **Compression**: Calls external `zstd` (binary must be in `$PATH`) → `<backup_id>.tar.zst`.
10. **Encryption**: Calls external `age` (must be installed) → `<backup_id>.tar.zst.age`. Recipients are read from `config/keys/recipients` (or default to self‑generated key pair).
11. **Verification**:
    - Compute SHA‑256 of the encrypted file.
    - Write `<backup_id>.tar.zst.age.sha256` to `backupvault/hashes/`.
    - Compare against expected (if any).
12. **Storage**:
    - Move encrypted archive to `backupvault/archives/`.
    - Move manifest JSON to `backupvault/manifests/`.
    - Optionally invoke `SyncEngine().sync()` to push to external targets (USB, Backblaze, S3).
13. **Cleanup**:
    - Call `cleanup_workspace()` to delete the workspace directory (preserving hardlink farm if incremental).
14. **Finalize**:
    - Call `session.finish()` and `report.finish()`.
    - Write report JSON to workspace (before cleanup) and also to `backupvault/manifests/` as a backup report.
    - Send email/Telegram report via `lib.reporting.send_backup_report()`.
    - Close logger.

### Error Handling
- Each major step is wrapped in a `try/except`. Exceptions are logged, recorded in the report, and may trigger an alert.
- If a fatal error occurs before storage (e.g., archive creation fails), the workflow exits early; the workspace is left intact for debugging (can be cleaned manually).

### Reporting
- `BackupReport` collects:
  - `backup_id`, timestamps, lists of executed/skipped/failed collectors.
  - Per‑collector stats (items processed, warnings, errors).
  - Archive size, compression ratio, encryption status.
  - SHA‑256 hash.
  - Final status (`SUCCESS`, `PARTIAL`, `FAILED`).
- Report is serialized as JSON and optionally rendered as Markdown for email.

---

## Utilities & Helper Modules

### `lib/logger.py`
- Thin wrapper around Python’s `logging` module.
- Configures a file handler (rotating not implemented; each run gets its own log file) and a stream handler for console.
- Provides `info()`, `warning()`, `error()`, `section()`, `end_section()` for readable log blocks.

### `lib/secrets.py`
- **`load_env()`**: Reads `.env`, sets `KEEPASS_DATABASE`/`KEEPASS_PASSWORD`, then invokes `_fetch_secrets()` to pull all known secret names from KeePass.
- **`get_config()`**: Simple `key=value` parser for `backup.conf`.
- Centralizes all secret retrieval logic; easy to swap backend (e.g., to Vault) by modifying this file.

### `lib/alert.py`
- Wrappers for `send_email_alert()` and `send_telegram_alert()`.
- Reads relevant settings from `backup.conf` (or environment) and uses `smtplib` or `requests` to send notifications.

### `lib/reporting.py`
- `send_backup_report(day_dir, log_file)`: Reads the log tail, builds a short summary (status, timestamp, what ran), attaches the log, and emails/Telegrams it per configuration.
- Uses MIME for email attachments.

### `storage/`
- `repository.py`: Abstract base class for storage backends; provides `put(object_name, data_stream)`, `get(object_name)`, `list()`, `delete()`.
- Concrete implementations:
  - `LocalRepository`: writes to filesystem (used for primary vault).
  - `BackblazeRepository`: uses `b2` SDK (if `BACKBLAZE_ENABLED=true`).
  - `USBRepository`: mounts a known USB device path and copies.
- `sync.py`: `SyncEngine` iterates over enabled destinations and calls their `sync()` method, which walks the local `backupvault/` and uploads missing/changed files.

### `orchestrator/scheduler.py`
- Reads/writes a JSON file (`~/.honestbackup/scheduler.json`) storing the last successful run timestamp for each collector.
- `mark_complete(name)`: records when a collector last finished. It no longer decides *whether* one runs - the clock times in `backup.conf` are the whole schedule.
- `mark_complete(name)`: writes current timestamp.

### `orchestrator/id.py`
- Simple UUIDv4 or timestamp‑based ID generation: `datetime.now().strftime("%Y%m%d%H%M%S") + "-" + uuid4().hex[:6]`.

---

## Text User Interface (TUI)

### Purpose
Provide an **interactive, discoverable** way to:
- View system status and recent logs.
- Launch backups on demand.
- Run the setup wizard for first‑time configuration.
- Edit non‑secret configuration (`backup.conf`, `.env`).
- Initiate and monitor restores.
- Access help and keyboard shortcuts.

### Technology Choice: **Textual**
- **Declarative UI** (similar to React) but in pure Python.
- Built‑in support for **asynchronous workers**, **modal screens**, **reactive properties**.
- Actively maintained, with good documentation.
- Allows us to create a **single‑code‑base TUI** that runs on Unix‑like terminals (Linux, macOS, WSL) without requiring ncurses expertise.

### Why a Pure Black (`#000000`) Theme?
- Target audience includes users running the TUI on laptops with **OLED/AMOLED** screens.
- True black turns off pixels completely, yielding:
  - **Maximum contrast** (white/cyan on black is easiest to read).
  - **Power savings** (especially important for mobile/embedded deployments).
  - Aesthetic alignment with modern “dark‑mode” flagship applications (e.g., VS Code Dark+, Obsidian, etc.).
- The accent color **cyan `#00ffff`** was chosen for its high visibility against black and its association with “information” / “cyber” themes.

### Screen Hierarchy

```
HonestbackupTUI (App)
 ├─ HeaderBlock (static title + mode + clock)
 ├─ ScrollableContainer (Viewport)  <-- Log widget
 └─ FooterBlock (Command Input + Mode Indicator)
```

#### Modal Screens (overlay the above)
- **SetupWizardScreen** – 6‑step linear wizard (Welcome → .env → config → secrets → test → complete).
- **ConfigEditorScreen** – Tabbed view for `backup.conf` (switches/inputs) and `.env` (key/value table).
- **RestoreWizardScreen** – Two‑step: select backup → specify restore options → confirm.
- **HelpScreen** – static text with commands and shortcuts.

### State Management
- Reactive attribute `current_mode` (`"Monitor"`, `"Plan"`, `"ManualSync"`).
- `ModeIndicator` widget in the footer updates automatically when `current_mode` changes (`watch_current_mode`).
- `HeaderBlock` updates its contents via `update_content(mode)` each second (clock tick) and on mode change.

### Input Handling
- Bottom `Input` widget receives keystrokes.
- **Enter** → `action_submit_command()`:
  - Strips whitespace.
  - If input starts with `:` → treat as colon command (`:backup`, `:setup-wizard`, etc.).
  - Else → log as unknown command.
- **Esc** → `action_clear_input()` clears the input buffer.
- **Tab** → `action_switch_mode()` cycles through the three modes.
- **Ctrl+P** → `action_show_help()` pushes the HelpScreen.
- **q** → `action_quit_app()` calls `self.exit()`.

### Worker Integration
- Long‑running operations (e.g., backup execution) are delegated to a **ThreadWorker** via `self.run_worker(self._backup_worker, thread=True)`.
- The worker calls the genuine `orchestrator.run.main()` (with `sys.argv` set to simulate `python -m orchestrator.run --force`).
- While the worker runs, the log widget receives messages via the `_log()` helper (which writes to the main Log widget).
- This separation keeps the UI responsive.

### Security & UI
- The TUI never displays secret values; even in the ConfigEditor, fields that correspond to known secrets (e.g., `CLIENT_SECRET`) are shown as password‑type inputs **but are not populated** (the UI only allows editing the placeholder; actual values remain in KeePass).
- The `.env` editor only allows editing `KEEPASS_DATABASE` and `KEEPASS_PASSWORD`; any other keys are ignored on save (with a warning.

### Extensibility
- Adding a new screen: subclass `ModalScreen`, implement `compose()` and any needed `on_button_pressed` handlers.
- Adding a new colon command: add a clause in `_handle_colon_command()`.
- Adding a new mode: extend the `MODES` list and update `ModeIndicator` formatting.

### First‑Run Experience
- On first launch, the TUI’s footer shows `> `:prompt.
- The user can immediately type `:setup-wizard` to be guided through:
  1. Welcoming message and overview.
  2. Creating/editing `.env` with KeePass location and password.
  3. Reviewing `backup.conf` (toggles, run times).
  4. Instructions on what secrets to enter into KeePass (list of expected entry titles).
  5. Placeholder for connection testing (future).
  6. Completion screen with next steps (`:backup` to run first backup, `:config` to tweak settings).

---

## Extending the System

### Adding a New Collector
1. Create a new directory under `collectors/` (e.g., `collectors/github/`).
2. Implement `collector.py` with a function `collect(workspace_path: str, logger: Logger) -> dict` that returns a dict of statistics (items processed, warnings, errors).
3. Add a wrapper in `orchestrator/`:
   - Define `run_<name>(workspace, logger)` that calls the collector and handles exceptions.
   - Import and add to the `ENABLE_*` list in `get_config()` (or read a new config flag).
   - Add scheduler support (optional): add a new entry in `scheduler.py`’s `INTERVALS` dict if you want periodic runs.
4. Update `backup.conf.example` with a new `ENABLE_<NAME>=false` line.
5. Document the data collected in the README/DOCUMENTATION.

### Adding a New Storage Destination
1. Subclass `storage.repository.BaseRepository` and implement `put`, `get`, `list`, `delete`.
2. Register the class in `storage/sync.py`’s `DESTINATION_MAP` dictionary keyed by a config flag (e.g., `ENABLE_GCS=True`).
3. Add the relevant configuration keys to `backup.conf.example` (e.g., `GCS_BUcket`, `GCS_SERVICE_ACCOUNT_JSON`).
4. Ensure any required SDK/library is listed in `requirements.txt`.

### Changing the Encryption / Compression Scheme
- Modify the constants in `orchestrator/archive.py` (compression command) and `orchestrator/encrypt.py` (encryption command).
- Keep the interface (`build_archive`, `encrypt_archive`) the same; only the underlying subprocess call changes.
- Update documentation accordingly.

### UI Themes (Alternative Color Schemes)
- Edit the `CSS` string inside `HonestbackupTUI` class.
- Expose a `--theme` command line flag that selects a preset CSS block (e.g., “default”, “high‑contrast”, “solarized”).

---

## Testing & Debugging

### Unit Tests
- Located in the `tests/` directory (not shown in the current snapshot but recommended).
- Use `pytest`.
- Mock external dependencies:
  - Microsoft Graph → `responses` library or `unittest.mock`.
  - Cloudflare API → `responses`.
  - Notion API → `responses`.
  - `keepassxc-cli` → subprocess mock returning predetermined secrets.
  - `zstd` / `age` → either rely on binaries being present in test environment or mock `subprocess.run`.

### Integration Tests
- Spin up a temporary directory as `WORKSPACE`.
- Use a **throwaway KeePass database** (created via `keepassxc-cli`) with dummy secrets.
- Run `python3 -m orchestrator.run --force --incremental` (or without incremental) and verify:
  - Expected files appear in `workspace/`.
  - Archive, hash, manifest are created in `backupvault/`.
  - Log file contains expected sections.
  - No secret appears in plain text in logs or config.

### Manual Debugging
- Increase log verbosity: modify `logger.py` to set level to `DEBUG`.
- Use `--force` to guarantee collectors run.
- Inspect the intermediate workspace after a run (if you skip cleanup by setting a breakpoint or using `--no-cleanup` - a flag you could add).
- Check the output of `keepassxc-cli show ...` directly to verify secret retrieval.

### Common Issues
| Symptom | Likely Cause | Fix |
|---------|--------------|-----|
| `keepassxc-cli: command not found` | KeePassXC not installed or not in `$PATH`. | Install via package manager (`sudo apt install keepassxc`) or add to PATH. |
| `Secret not found for <NAME>` | Either the KeePass entry title does not match exactly, or the database is locked/wrong password. | Ensure entry title matches exactly (case‑sensitive). Verify `KEEPASS_DATABASE` points to the correct `.kdbx`. Test manual `keepassxc-cli show ...`. |
| Archive creation fails with “tar: Cannot stat: No such file or directory” | Workspace directory missing or empty due to collector failure. | Check collector logs; ensure the collector actually wrote files. |
| Encryption fails: `age: exit status 1` | Recipient file missing or malformed, or age binary not found. | Ensure `age` is installed (`brew install age` or `sudo apt get install age`). Verify `config/keys/recipients` contains a valid age public key line (`age1...`). |
| Incremental backup fails hardlink error: “Operation not permitted” | `WORKSPACE` and `BACKUP_TARGET` are on different filesystems. | Move both to the same mount point, or disable incremental (`INCREMENTAL=false`). |
| Log shows “Nothing scheduled to run.” but you expected collectors | Either nothing is enabled, or `--only` named a collector that is switched off. | Check the switches on the TUI's Scheduling screen, and the `--only` list in `crontab -l`. |

---

## Deployment & Operations

### Installation
1. **Clone the repository**:
   ```bash
   git clone https://github.com/5h1Vm/honest-backup.git
   cd honnestbackup
   ```
2. **Install system dependencies**:
   - `keepassxc-cli` (from KeePassXC)
   - `zstd` (zstandard)
   - `age` (age encryption)
   - `python3 >= 3.9`
   - (Optional) `playwright` for Notion browser export: `pip install playwright` and run `playwright install-deps`.
3. **Create a virtual environment** (recommended):
   ```bash
   python3 -m venv .venv
   source .venv/bin/activate
   pip install -r requirements.txt
   ```
4. **Configure KeePass**:
   - Create a `.kdbx` database (if you don’t have one).
   - Add entries for each secret listed in the README (title = env var name, password = actual secret).
5. **Set up `.env`**:
   ```bash
   echo "KEEPASS_DATABASE=/path/to/your/secrets.kdbx" > .env
   echo "KEEPASS_PASSWORD=your_master_password" >> .env
   chmod 600 .env
   ```
6. **Review and edit `config/backup.conf`**:
   - Set `WORKSPACE` and `BACKUP_TARGET` to suitable directories (preferably on the same filesystem).
   - Enable/disable collectors as needed.
   - Adjust the times each service runs at.
   - Set `INCREMENTAL=true` if desired.
   - Configure notification sections (email, Telegram) if you want alerts.
7. **Run a test backup**:
   ```bash
   python3 -m orchestrator.run --force
   ```
   Check output, verify files in `backupvault/`, and ensure no errors.
8. **Automate with cron** (example: daily at 2 AM):
   ```bash
   crontab -e
   # Add line:
   0 2 * * * /path/to/honestbackup/run_backup.sh
   ```
   where `run_backup.sh` merely calls `python3 -m orchestrator.run`.

### Backup Verification
- To verify an archive:
  ```bash
  age -d -i /path/to/private_key.age backupvault/archives/20230815-123456-abcd12.tar.zst.age > /tmp/test.tar.zst
  unzstd /tmp/test.tar.zst -o /tmp/test.tar
  tar -tf /tmp/test.tar | head   # list contents
  ```
- Compare the SHA‑256 in `backupvault/hashes/20230815-123456-abcd12.sha256` with the recomputed hash.

### Restoring Data
- Use the CLI or the TUI’s **Restore Wizard**:
  - CLI: `python3 -m orchestrator.run --restore --list` to see available backups.
  - Then: `python3 -m orchestrator.run --restore --backup-id <ID> --restore-dir ./restore --private-key /path/to/key.age`
- The TUI’s guide walks you through selecting a backup from a table, specifying a restore directory, optional filename filter, and confirming.

### Upgrading
- Pull latest changes: `git pull`.
- Review `CHANGELOG.md` (if present) for any breaking changes.
- Re‑install Python dependencies if `requirements.txt` changed: `pip install -r requirements.txt`.
- Verify that your existing `.kdbx` still contains all necessary entries (new collectors may require new secrets).

---

## Glossary

| Term | Meaning |
|------|---------|
| **Collector** | A module that extracts data from a specific SaaS service (M365, Cloudflare, Notion). |
| **Workspace** | A timestamped directory (`YYYY-MM-DD`) under `WORKSPACE` where raw collected files are stored for a single backup run. |
| **Manifest** | A JSON file listing every file in the workspace with metadata (size, relative path, SHA‑256 if desired). |
| **Archive** | A POSIX tar file (`*.tar`) containing the whole workspace directory tree. |
| **Compression** | Application of Zstandard (`zstd`) to the tar file, producing `*.tar.zst`. |
| **Encryption** | Encryption of the compressed archive using `age`, producing `*.tar.zst.age`. |
| **Backup Vault (`backupvault/`)** | The destination directory holding final artifacts: subdirectories `archives/`, `hashes/`, `manifests/`. |
| **Incremental Backup** | A strategy where the new workspace is created by hardlinking unchanged files from the previous backup, storing only deltas. |
| **KeePass** | An offline, open‑source password safe; used as the sole source of secrets for HonestBackup. |
| **Age** | A modern, simple, and encrypt‑only tool (based on X25519) used for encrypting backup archives. |
| **Zstandard (zstd)** | A fast lossless compression algorithm, offering better ratio than gzip with competitive speed. |
| **Scheduler** | Internal component that records when each collector last finished, for display. Scheduling itself is done by cron, from the clock times in `backup.conf`. |
| **TUI** | Text User Interface built with the Textual framework, providing an interactive, keyboard‑driven frontend. |

---

## Closing Remarks

HonestBackup merges **strong security practices** (zero secret leakage, encryption‑at‑rest, offline secret storage) with **practical usability** (automated scheduling, incremental storage, optional interactive TUI). Its design is deliberately modular so that new services, storage backends, or cryptographic primitives can be added without disturbing the core pipeline.

Developers wishing to contribute should:
1. Familiarize themselves with the collector interface (`collectors/<name>/collector.py`).
2. Respect the security modelnever log or store raw secrets.
3. Keep the UI responsive by offloading long‑running work to threads or processes.
4. Write tests that mock external APIs and verify that the pipeline produces the expected artifacts.

With this documentation in hand, you should be able to understand, maintain, and extend HonestBackup confidently. Happy backing‑up!