# The office copy

A third copy of every backup, on a drive you can physically hold.

The server writes to Backblaze. This folder is what runs on the **office
laptop** to bring that down onto an external drive and keep it there.

The drive is not where the only copy of anything should live — Backblaze is
the copy that must always be complete. The drive exists for the one failure
Backblaze cannot cover: losing Backblaze itself. A compromised server holding
a write key, a billing lapse, an account dispute. It is the offline, offsite,
out-of-band copy, and `reseed.py` is how it puts the cloud back.

Nothing here talks to Microsoft 365, Cloudflare or Notion. It only moves
finished, encrypted archives. It never deletes anything from the drive.

---

## What to install on the laptop

```bash
sudo apt install rclone python3
```

Nothing else. These scripts use only the Python standard library, so there is
no `pip install` step and nothing to keep updated.

(On Windows it would be `winget install Rclone.Rclone` plus Python from
python.org with "Add Python to PATH" ticked.)

## Setting it up, once

1. Copy this whole `office` folder onto the laptop.

2. Make a **read-only** Backblaze key for this laptop. Do not reuse the
   server's key: the server needs write access, the laptop does not, and a
   laptop is far easier to lose. In the Backblaze console:

   *Account → Application Keys → Add a New Application Key*
   - Name: `office-laptop-readonly`
   - Allow access to bucket: **the backup bucket only**
   - Type of access: **Read Only**

   Copy the key ID and the key itself — Backblaze shows the key once.

3. Create the connection:

   ```bash
   rclone config
   ```

   Choose `n` for a new remote, name it `backblaze`, pick **Backblaze B2**,
   and paste the two values from the previous step. Accept the defaults for
   everything else.

   Check it works:

   ```bash
   rclone lsd backblaze:your-bucket
   ```

4. Copy `pull.conf.example` to `pull.conf` and set the two values at the top:
   the remote (`backblaze:your-bucket`) and where the drive is mounted. Find
   the mount point with `lsblk` — it is usually `/media/<you>/<drive label>`.

5. Plug the drive in and run it:

   ```bash
   ./copy-now.sh
   ```

   The first run downloads the whole history. Later runs only fetch what is
   new.

6. Optional, but worth it — put it on the desktop and on a timer:

   ```bash
   ./install-desktop.sh
   ./install-schedule.sh 19:00
   ```

## Running it

| Command | What it does |
|---|---|
| `./copy-now.sh` | The double-click one: friendly, waits at the end |
| `python3 pull.py` | The same copy and verify, plain output |
| `python3 pull.py --dry-run` | Show what would be copied, copy nothing |
| `python3 pull.py --verify-all` | Re-checksum every archive already on the drive |
| `python3 pull.py --status` | Report on the drive without touching the network |

Every archive is checked against the SHA-256 the server produced when it made
it. If a file on the drive has rotted, the next run notices — and because
rclone is told to compare checksums, a damaged file that Backblaze still holds
is quietly replaced with a clean copy.

## The easy way: double-click it

Run this once:

```bash
./install-desktop.sh
```

That puts **"HonestBackup — Copy to Drive"** on the desktop and in the
applications menu. From then on: plug the drive in, double-click, watch it
work. It opens a terminal window, says what it copied, and waits for a
keypress so the window does not vanish before it can be read.

It checks the obvious things first and says so in plain words — Python
missing, rclone missing, not set up yet, drive not plugged in — rather than
throwing a traceback at whoever is standing there.

## Or on a schedule

```bash
./install-schedule.sh 19:00      # daily at 19:00
./install-schedule.sh --status   # what is scheduled, and when it last ran
./install-schedule.sh --remove   # stop
```

**This uses a systemd timer, not cron, and the difference matters on a
laptop.** Cron fires at a moment in time; if the machine is asleep, shut, or
switched off at that moment, the run is skipped and cron never goes back for
it. A laptop closed at 18:55 would silently miss a 19:00 copy every single
day, and nothing would say so.

The timer is created with `Persistent=true`, which records when the job last
ran and fires it as soon as the machine is back if it was missed. It also
enables lingering so it works when nobody is logged in. If systemd is not
available the script falls back to cron and warns you about the catch-up
problem.

The time you give is in the **laptop's own time zone** — set the laptop to
IST and 19:00 means 19:00 IST. (The server needs an explicit time zone
because it runs on UTC; the laptop does not.)

If the drive is unplugged the run stops immediately and changes nothing, and
the next run picks it up.

**Windows**, if it is ever a Windows machine — Task Scheduler, new task,
daily at a time the laptop is on:

```
Program:    C:\HonestBackup\office\run.bat
Start in:   C:\HonestBackup\office
Arguments:  --scheduled
```

Tick "Run whether user is logged on or not". Task Scheduler has a
"Run task as soon as possible after a scheduled start is missed" option —
tick that too; it is the equivalent of `Persistent=true`.

---

## Seeing it from the server

After each run the laptop publishes a small status file to
`<bucket>/status/<site>.json` — how many backups it holds, the oldest and
newest, when it last ran, and whether anything failed its checksum. The
server's terminal interface shows that on the main screen as **Office copy**,
so you can tell at a glance whether the office drive is current without
walking over to it.

The status file contains no credentials and no backup content. Turn it off
with `PUBLISH_STATUS=false` if you would rather it did not exist.

---

## Refilling the cloud from the drive

`reseed.py` is the reverse direction, and it is the reason the drive earns its
keep. Use it when the cloud side no longer holds the full history — a new
Backblaze account, a new bucket, a different provider, or a bucket that was
emptied.

```bash
python3 reseed.py --target backblaze-new:your-bucket --dry-run
python3 reseed.py --target backblaze-new:your-bucket
```

It works out what the target is missing, verifies those archives on the drive
first (it will not push a damaged file up as though it were good), and copies
them. Then point `BACKBLAZE_REMOTE` in the server's `config/backup.conf` at
the new bucket and the next nightly backup carries on into it.

**Rotating a Backblaze key is not one of these cases.** A key is permission to
reach the data, not the data itself. Change the key, update the remote with
`rclone config` on both the server and the laptop, and every archive is
exactly where it was. Nothing moves.

---

## Restoring from the drive

The archives on the drive are the same encrypted files the server holds:

```
<id>.tar.zst.age   →   age -d   →   zstd -d   →   tar -x
```

They can only be opened with the **age private key**, which lives in the
server's credential store and is not on this laptop. That is deliberate: a
stolen drive is not a data breach.

To restore, use the server's terminal interface, which does the whole
sequence. If the server itself is gone, restoring means installing `age`,
`zstd` and `tar`, and supplying the key from wherever it is held offline.

---

## What the drive ends up holding

```
/media/you/HonestBackup/
    archives/      <id>.tar.zst.age      the encrypted backups
    hashes/        <id>.sha256           what each one should hash to
    manifests/     <id>.manifest.json    what is inside each one
    reports/                             the daily written reports
    metadata/                            the repository index
    office-index.json                    every backup on this drive
    office-status.json                   the last run's result
```

Same shape as the server's vault and the Backblaze bucket, so any of the three
can stand in for the others.
