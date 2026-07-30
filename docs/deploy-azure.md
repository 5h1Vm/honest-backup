# Deploying to Azure Container Apps

Read the first section before the rest. The reason for moving off the VM was
cost, and the numbers do not support it as clearly as they looked.

---

## The cost, honestly

| | per month | why |
|---|---:|---|
| Container Registry — Basic | ~$5 | 10 GB included; our image is 3.88 GB |
| Container Apps Job — compute | ~$3 | 1 vCPU / 2 GB × ~40 min a day |
| **Premium file share (NFS)** | **~$16** | **100 GiB minimum, ~$0.16/GiB provisioned** |
| Egress and operations | ~$1 | |
| **Total** | **~$25** | |
| *The current VM* | *$15–30* | *B2s-class with a 29 GB disk* |

**So this is roughly a wash, not a saving.**

Two things cause that, and neither was obvious up front:

**Premium storage is not optional.** The workspace is hardlinked from the
previous run — that is what makes the backup incremental. Azure Files over
SMB does everything else a backup needs and does not support hardlinks.
Hardlinks need an NFS share, NFS shares are premium tier only, and premium
shares bill for **100 GiB minimum whether you use it or not**. We need about
5 GB. The other 95 GB is a floor, not a choice.

**The registry is a fixed cost.** ACR Basic is ~$5/month to hold one 3.88 GB
image — as much as the compute it serves.

Standard SMB would drop storage from ~$16 to ~$6, bringing the total to
about $15. But then hardlinks are gone, incremental is off, and every run
re-downloads ~80 MB of SharePoint that has not changed. That is precisely
the fault we just fixed, reintroduced deliberately to save $10 a month.

Prices are list, for Central India, and change. Check them against the Azure
pricing calculator for your subscription before committing — the shape of the
argument matters more than the exact figures.

### Using GitHub instead of the Azure registry

`ghcr.io` is free for public images and removes the ~$5, bringing the total
to about $19. Container Apps pulls from it like any other registry.

**It does not expose credentials.** The image has never contained any: the
`.dockerignore` keeps `.env`, `secrets.kdbx`, `config/keys/` and
`config/backup.conf` out of every layer, and that was verified by searching
the built image. Credentials arrive at runtime from the mounted volume, which
is the whole reason they are mounted rather than copied in.

Two things to know before choosing it:

- **The image has to be public to be free.** GitHub Packages allows 500 MB
  for private packages on the free tier and this image is 3.88 GB. A public
  image exposes the code and the installed packages — and this repository is
  already public, so that is no new exposure. It would be if the repository
  were ever made private.
- **GitHub cannot hold the data.** A registry stores images, not volumes.
  The workspace, credentials and Notion profile still need the Azure share,
  and that is the ~$16 that dominates the bill either way.

GitHub Actions can also build the image on push, replacing `az acr build` —
free for public repositories.

> **Worth checking:** the README describes this as "an internal tool, not a
> public release … not licensed for redistribution", but the repository is
> publicly readable. No secrets are in it — the history was scanned — so
> nothing is leaked. But the two statements disagree, and it is worth
> deciding which one you meant.

### So should you?

**For cost alone: no.** The VM is already competitive and simpler.

**Worth doing anyway if** you want the backup to stop depending on one
machine you have to patch and keep alive, want it reproducible from a
Dockerfile, or are consolidating onto Azure regardless. Those are real
reasons. Saving money is not one of them here.

**One thing you would lose:** the TUI. A Job exists only while it runs, so
there is nothing to attach to between backups. Day to day you would work from
the emailed reports and the logs.

---

## What is already done

Everything except the deploy itself, and all of it tested on real hardware:

| | |
|---|---|
| `Dockerfile` | builds clean; verified on the office laptop |
| Headless Chromium | launches in-container — `HeadlessChrome/149` |
| **A real backup** | ran in the container: archive decrypts, hash verifies |
| **Notion export** | logged in and downloaded `markdown_export.zip` |
| M365 collection | 13.2 MB archive produced in-container |
| Email + Telegram | delivered from inside the container |
| No secrets in image | `.env`, `secrets.kdbx`, keys all absent |
| No root-owned files | `--user` mapping verified |
| `deploy/azure.sh` | written, syntax-checked, **not run** |

The deploy script has not been executed — that needs an Azure subscription,
and this machine has no credentials for one.

---

## The guide

### Before you start

```bash
az login
az account set --subscription "<the one you want>"
export KEEPASS_PASSWORD='<the master password from .env>'
```

Edit the names at the top of `deploy/azure.sh`. `REGISTRY` and `STORAGE`
must be globally unique across Azure, so `honestbackupacr` may be taken.

### 1. Run it

```bash
./deploy/azure.sh
```

It creates the resource group, registry, storage, environment and job, and
builds the image **in Azure** rather than pushing 4 GB from your connection.
Every step is idempotent, so re-running after a failure carries on.

Expect 15–25 minutes, most of it the image build.

### 2. Seed the volume — the job fails until you do

The job has the code but nothing that makes it *this* installation:

```
secrets.kdbx              the credential database
config/keys/archive.key   the encryption key
config/backup.conf        recipients, retention, what is enabled
honestbackup-profile/     Notion's logged-in browser session
```

Mount the share and copy them in:

```bash
sudo mkdir -p /mnt/hb
sudo mount -t nfs <storage>.file.core.windows.net:/<storage>/<share> /mnt/hb \
    -o vers=4,minorversion=1,sec=sys
sudo cp secrets.kdbx /mnt/hb/
sudo cp -r config honestbackup-profile /mnt/hb/
sudo mkdir -p /mnt/hb/{workspace,backupvault,state,logs}
```

**The Notion profile is the awkward one.** A container cannot log in to
Notion — nobody is there to answer the prompt. Sign in on a machine with a
screen, then copy that profile up. When the session eventually expires, a
human has to do it again. That is the one part of this that is not
unattended.

### 3. Run it once by hand

Do not wait for the schedule to find out.

```bash
az containerapp job start -g honestbackup-rg -n honestbackup-nightly
az containerapp job execution list -g honestbackup-rg -n honestbackup-nightly -o table
az containerapp job logs show -g honestbackup-rg -n honestbackup-nightly \
    --container honestbackup-nightly --follow
```

Watch for `Backup Finished` and an email. If SharePoint reports
`0 unchanged` on the *second* run, the volume is not preserving hardlinks and
you are on an SMB share by mistake.

### 4. Decommission the VM only after two clean nights

Keep it running in parallel until you have seen two scheduled runs finish and
two reports arrive. Turning the old one off first is how a migration becomes
an outage nobody notices for a week.

---

## If it goes wrong

| symptom | cause |
|---|---|
| refuses to start, "filesystem without hardlinks" | SMB share instead of NFS — this is the check working |
| killed partway through Notion's export | under 2 GB memory; Chromium was OOM-killed |
| job times out at 30 minutes | `replicaTimeout` not raised; a full run takes 17–25 min |
| Notion stops at a login page | the session expired — re-seed the profile from a signed-in machine |
| `credential database` errors | `secrets.kdbx` not copied to the share, or `KEEPASS_PASSWORD` wrong |
