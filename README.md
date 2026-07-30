# HonestBackup

Backs up Microsoft 365, Cloudflare and Notion to encrypted archives you own.

Most SaaS backup tools are a subscription and a promise. This is a script you
run on your own machine, writing `age`-encrypted archives to your own storage.
Nothing can read them without your key - not the cloud provider, not us.

It exists because Microsoft keeps the Unified Audit Log for 180 days and
sign-in logs for 30, and compliance frameworks tend to ask for years.

![The main menu](docs/home.svg)

---

## What it backs up

| | |
|---|---|
| **Microsoft 365** | Unified Audit Log, Entra sign-in and directory logs, Defender alerts and incidents, Conditional Access, users, groups, roles, deleted objects, mail, calendars, contacts, SharePoint and OneDrive files |
| **Cloudflare** | Zones, DNS, WAF and firewall rules, Zero Trust config, account audit log |
| **Notion** | Full workspace export, database schemas and rows |

What is and isn't collected - and why - is written up in
[docs/coverage.md](docs/coverage.md).

## How it works

```
collect → manifest → tar → zstd → age encrypt → SHA-256 → store
```

Archives go to three places, each with its own retention: the local machine,
Backblaze B2, and an external drive. Every archive is hash-verified on write
and can be re-verified at any time.

Backups are incremental. The first run downloads everything; later runs fetch
only what changed, using API delta tokens where the provider offers them and
file size comparison where it doesn't.

---

## Getting started

**You need:** Python 3.11+, `age`, `zstd`, `rclone`, `keepassxc-cli`.

```bash
git clone https://github.com/5h1Vm/honest-backup.git
cd honest-backup
./setup.sh
```

Then open the terminal interface and pick **First-time setup**:

```bash
python3 -m orchestrator.run --tui
```

It walks through the credentials, storage and schedule. Nothing needs editing
by hand, though `config/backup.conf.example` documents every setting if you'd
rather.

### Credentials

API keys live in an encrypted credential database, never in config files or
environment variables. The setup wizard creates it. You'll need:

- **Microsoft 365** - an Entra app registration with application permissions
  (`AuditLogsQuery.Read.All`, `SecurityEvents.Read.All`, `Sites.Read.All`,
  `Mail.Read`, and friends), admin-consented. SharePoint file access
  additionally needs a certificate - Graph won't accept a client secret for it.
- **Cloudflare** - an API token with read access to the zones and account.
- **Notion** - an internal integration token.
- **Backblaze B2** - an application key scoped to one bucket.

---

## Using it

```bash
python3 -m orchestrator.run --tui           # the interface
python3 -m orchestrator.run --force         # back up now
python3 -m orchestrator.run --check-cloud   # is the cloud copy complete?
```

Everything is reachable from the interface, which works entirely by keyboard 
arrow keys, Enter, Escape.

### Scheduling

Each service keeps its own run times, set in whichever time zone you think in.

![Scheduling](docs/scheduling.svg)

Those times are merged into a crontab: every distinct moment becomes one entry
naming the services due then, so services sharing a time share a single run.
The conversion to the machine's own zone happens before cron sees it, because
Ubuntu's cron ignores `CRON_TZ` and would otherwise run your backup hours away
from where you wanted it.

### Restoring

![Backups](docs/backups.svg)

Pick a backup, pick files, restore. Or by hand:

```bash
age -d -i key.txt backup.tar.zst.age | zstd -d | tar -x
```

The archive format is deliberately boring. If this project vanishes, three
standard tools still open your data.

### Reading what's inside

![Logs](docs/logs.svg)

Decrypted archives can be browsed in place - JSON is pretty-printed, and ZIPs
(including the nested ones Notion exports) open without extracting.

---

## Knowing the backups are actually there

A file listing only tells you what *is* in your bucket. It can't tell you what
*should* be - so a failed upload, a deleted archive, or a credential pointing
at the wrong bucket all look perfectly healthy.

So every sync appends to a ledger: backup id, size, SHA-256, where it went.
The ledger is stored on the server, in the bucket, and on the external drive.

```bash
python3 -m orchestrator.run --check-cloud
```

```
  Cloud:    backblaze:your-bucket
  Ledger:   412 backups, 23,104,882,110 bytes

  the cloud holds all 412 archives the ledger expects
  OK
```

Point it at an empty bucket and it says `412 expected - 412 missing` instead of
quietly agreeing with itself.

## The office copy

[`office/`](office/README.md) is a standalone folder for a second machine that
keeps a copy on an external drive. It pulls from B2, verifies every archive
against its hash, and never deletes anything. `reseed.py` goes the other way 
refilling a lost or migrated bucket from the drive.

It holds no API keys and no encryption key, so a stolen drive is not a breach.

---

## Notes

**Retention is per destination.** Local is short, cloud is long, the drive is
forever. Nothing should ever be down to one copy - at these volumes cloud
storage costs about a dollar a month per decade of history, so expiring the
cloud copy rarely pays for the risk.

**The encryption key is the whole thing.** Lose it and the archives are noise.
Rotating it is supported and keeps the old key for old archives, but keep a
copy somewhere offline.

**Licences limit what Microsoft will hand over.** Risky-user scoring needs
Entra ID P2, mail threat telemetry needs Defender for Office 365 P2. The tool
reports these as known limits rather than failures, so a warning means
something is genuinely wrong.

## Layout

```
collectors/     one package per service
orchestrator/   the run itself: scheduling, archiving, retention
storage/        repository, sync, ledger
lib/            logging, reporting, credentials
tui/            terminal interface
office/         the external-drive copy, deployed separately
```

---

## Internal use

This is an internal tool, not a public release. It is not licensed for
redistribution and comes with no warranty or support.
