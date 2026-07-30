#!/usr/bin/env bash
#
# Deploy HonestBackup to Azure Container Apps as a scheduled job.
#
#   az login
#   ./deploy/azure.sh
#
# Everything is named from the variables at the top and every step is
# idempotent, so running it again after a failure carries on rather than
# starting over or making a second copy of anything.
#
# WHY A JOB AND NOT AN APP
#   A Container App expects a process that stays up and serves something.
#   This runs for twenty minutes and exits, which is a Job. Billing follows
#   the same distinction: a Job is charged for the minutes it runs, an App
#   for the hours it exists, and that difference is the whole reason for
#   moving off the VM.
#
# WHY NFS AND NOT THE USUAL SMB SHARE
#   The workspace is hardlinked from the previous run — that is what makes
#   the backup incremental. Azure Files over SMB does everything else a
#   backup needs and silently does not support hardlinks. HonestBackup now
#   refuses to run rather than quietly copying instead, so an SMB share here
#   produces a job that fails every night with a clear message. NFS shares
#   are premium-tier only, which is why the storage account below is
#   Premium_LRS and FileStorage.
#
# WHAT THIS DOES NOT DO
#   Log in for you, choose a subscription, or seed Notion's browser profile.
#   That last one needs a human once — see the note at the end.

set -euo pipefail

# ---------------------------------------------------------------------------
# names — change these, they are the only things that need editing
# ---------------------------------------------------------------------------
LOCATION="${LOCATION:-centralindia}"
RESOURCE_GROUP="${RESOURCE_GROUP:-honestbackup-rg}"
REGISTRY="${REGISTRY:-honestbackupacr}"          # must be globally unique
STORAGE="${STORAGE:-honestbackupstore}"          # must be globally unique
SHARE="${SHARE:-honestbackup-data}"
ENVIRONMENT="${ENVIRONMENT:-honestbackup-env}"
JOB="${JOB:-honestbackup-nightly}"
IMAGE_TAG="${IMAGE_TAG:-$(date +%Y%m%d-%H%M)}"

# 01:00 and 13:00 Asia/Kolkata, expressed in UTC because that is what cron
# in Container Apps uses. Change both numbers together if the times move.
CRON="${CRON:-30 19,7 * * *}"

BOLD=$'\033[1m'; DIM=$'\033[2m'; GREEN=$'\033[32m'; RED=$'\033[31m'; OFF=$'\033[0m'
step() { echo; echo "${BOLD}$*${OFF}"; }
ok()   { echo "  ${GREEN}✓${OFF} $*"; }
die()  { echo; echo "  ${RED}Stopped:${OFF} $*"; exit 1; }

command -v az >/dev/null || die "The Azure CLI is not installed."
az account show >/dev/null 2>&1 || die "Not logged in. Run: az login"
echo "  ${DIM}subscription: $(az account show --query name -o tsv)${OFF}"

step "1. Resource group"
az group create -n "$RESOURCE_GROUP" -l "$LOCATION" -o none
ok "$RESOURCE_GROUP in $LOCATION"

step "2. Container registry"
az acr create -g "$RESOURCE_GROUP" -n "$REGISTRY" --sku Basic --admin-enabled true -o none 2>/dev/null || true
ok "$REGISTRY.azurecr.io"

step "3. Build and push the image"
# Built by ACR rather than locally: the image is ~4 GB and pushing it from
# an office connection takes far longer than letting Azure build it beside
# the registry it is going into.
az acr build -r "$REGISTRY" -t "honestbackup:$IMAGE_TAG" -t "honestbackup:latest" . -o none
ok "honestbackup:$IMAGE_TAG"

step "4. Storage for the parts that must survive between runs"
az storage account create -g "$RESOURCE_GROUP" -n "$STORAGE" -l "$LOCATION" \
    --sku Premium_LRS --kind FileStorage --enable-large-file-share -o none 2>/dev/null || true
# NFS, not SMB. See the note at the top: SMB has no hardlinks and the backup
# will refuse to run on it.
az storage share-rm create -g "$RESOURCE_GROUP" --storage-account "$STORAGE" \
    -n "$SHARE" --quota 100 --enabled-protocols NFS -o none 2>/dev/null || true
ok "$SHARE (NFS, 100 GB)"

step "5. Container Apps environment"
az containerapp env create -g "$RESOURCE_GROUP" -n "$ENVIRONMENT" -l "$LOCATION" -o none 2>/dev/null || true
STORAGE_KEY=$(az storage account keys list -g "$RESOURCE_GROUP" -n "$STORAGE" --query "[0].value" -o tsv)
az containerapp env storage set -g "$RESOURCE_GROUP" -n "$ENVIRONMENT" \
    --storage-name hbdata --azure-file-account-name "$STORAGE" \
    --azure-file-account-key "$STORAGE_KEY" --azure-file-share-name "$SHARE" \
    --access-mode ReadWrite -o none
ok "$ENVIRONMENT, share mounted as hbdata"

step "6. The job"
ACR_USER=$(az acr credential show -n "$REGISTRY" --query username -o tsv)
ACR_PASS=$(az acr credential show -n "$REGISTRY" --query "passwords[0].value" -o tsv)

# replicaTimeout is 3600, not the 1800 default: a full run takes 17-25
# minutes and a first run with a seven-day audit catch-up takes longer.
# A job killed at 30 minutes leaves a half-written workspace behind.
#
# 2 GB is a floor, not a preference. Notion's export drives a real Chromium
# and below that it is killed partway through with an error that never
# mentions memory.
az containerapp job create \
    -g "$RESOURCE_GROUP" -n "$JOB" --environment "$ENVIRONMENT" \
    --trigger-type Schedule --cron-expression "$CRON" \
    --replica-timeout 3600 --replica-retry-limit 1 \
    --parallelism 1 --replica-completion-count 1 \
    --image "$REGISTRY.azurecr.io/honestbackup:$IMAGE_TAG" \
    --registry-server "$REGISTRY.azurecr.io" \
    --registry-username "$ACR_USER" --registry-password "$ACR_PASS" \
    --cpu 1.0 --memory 2.0Gi \
    --secrets keepass-password="${KEEPASS_PASSWORD:?export KEEPASS_PASSWORD first}" \
    --env-vars KEEPASS_DATABASE=/app/secrets.kdbx \
               KEEPASS_PASSWORD=secretref:keepass-password \
               NOTION_PROFILE_DIR=/app/honestbackup-profile \
    -o none 2>/dev/null || echo "  (job exists — updating)"

az containerapp job update -g "$RESOURCE_GROUP" -n "$JOB" \
    --image "$REGISTRY.azurecr.io/honestbackup:$IMAGE_TAG" -o none
ok "$JOB on '$CRON' (UTC)"

step "Done"
cat <<NEXT

  ${BOLD}The job is created, and it will fail until you seed the volume.${OFF}
  It has the code but none of the things a backup needs to be *this*
  installation rather than any other:

    secrets.kdbx              the credential database
    config/keys/archive.key   the encryption key
    config/backup.conf        recipients, retention, what is enabled
    honestbackup-profile/     Notion's logged-in browser session

  The first three you copy once. The fourth is the awkward one: a container
  cannot log in to Notion, because nobody is there to answer the prompt. Sign
  in on a machine with a screen, then copy that profile up.

  Mount the share somewhere and copy them in:

    ${DIM}sudo mount -t nfs $STORAGE.file.core.windows.net:/$STORAGE/$SHARE /mnt/hb -o vers=4,minorversion=1,sec=sys
    sudo cp secrets.kdbx /mnt/hb/
    sudo cp -r config honestbackup-profile /mnt/hb/${OFF}

  Then run it once by hand rather than waiting for the schedule:

    ${DIM}az containerapp job start -g $RESOURCE_GROUP -n $JOB
    az containerapp job execution list -g $RESOURCE_GROUP -n $JOB -o table${OFF}

  ${BOLD}One thing you lose:${OFF} the TUI. A Job exists only while it runs, so
  there is nothing to attach to between backups. Day to day you would read
  the emailed report and the logs instead:

    ${DIM}az containerapp job logs show -g $RESOURCE_GROUP -n $JOB --container $JOB${OFF}

NEXT
