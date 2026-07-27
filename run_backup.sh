#!/bin/bash
#
# HonestBackup wrapper script for cron execution with email reporting.
#
# This script runs the orchestrator, captures output, and emails a summary.
# It respects the configuration in config/backup.conf and environment variables.

set -euo pipefail

# Directory where this script resides
SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
cd "$SCRIPT_DIR"

# Load configuration (simple key=value parsing, ignores comments and blank lines)
CONFIG_FILE="$SCRIPT_DIR/config/backup.conf"
if [[ ! -f "$CONFIG_FILE" ]]; then
    echo "Error: Configuration file not found at $CONFIG_FILE"
    exit 1
fi

# Export variables from config (only lines with KEY=VALUE, no spaces around = unless quoted)
while IFS='=' read -r key value; do
    # Trim whitespace
    key=$(echo "$key" | xargs)
    value=$(echo "$value" | xargs)
    # Skip empty lines and comments
    if [[ -z "$key" || "$key" =~ ^# ]]; then
        continue
    fi
    # Remove surrounding quotes if present
    value="${value%\"}"
    value="${value#\"}"
    value="${value%'’'}"
    value="${value#’}"
    export "$key=$value"
done < <(grep -E '^[[:space:]]*[A-Z_][A-Z0-9_]*[[:space:]]*=' "$CONFIG_FILE" | sed 's/[[:space:]]*=[[:space:]]*/=/')

# Defaults
INCREMENTAL=${INCREMENTAL:-false}
FORCE=${FORCE:-false}

# Build command
CMD="python3 -m orchestrator.run"
if [[ "$FORCE" == "true" ]]; then
    CMD="$CMD --force"
fi
if [[ "$INCREMENTAL" == "true" ]]; then
    CMD="$CMD --incremental"
fi

# Anything passed to this script goes straight through to the orchestrator.
# Cron uses this to say which collectors are due at this particular time,
# e.g. run_backup.sh --only m365,cloudflare
if [[ $# -gt 0 ]]; then
    CMD="$CMD $*"
fi

# Output files
TIMESTAMP=$(date +"%Y-%m-%d_%H-%M-%S")
LOG_DIR="$SCRIPT_DIR/logs"
mkdir -p "$LOG_DIR"
LOG_FILE="$LOG_DIR/run_$TIMESTAMP.log"
ERR_FILE="$LOG_DIR/run_$TIMESTAMP.err"

echo "Starting HonestBackup run at $(date)" | tee "$LOG_FILE"
echo "Command: $CMD" >> "$LOG_FILE"
echo "----------------------------------------" >> "$LOG_FILE"

# Run the command, capturing stdout and stderr
{
    eval "$CMD" 2>>"$ERR_FILE"
    RESULT=$?
} || true

# Combine log and error for reporting
cat "$ERR_FILE" >> "$LOG_FILE"
echo "----------------------------------------" >> "$LOG_FILE"
echo "Finished at $(date) with exit code $RESULT" >> "$LOG_FILE"

# Determine status
if [[ $RESULT -eq 0 ]]; then
    STATUS="SUCCESS"
else
    STATUS="FAILURE"
fi

# Prepare email
SUBJECT="[HonestBackup] $STATUS - $(date +"%Y-%m-%d %H:%M:%S")"
TO="${EMAIL_TO:-root}"
FROM="${EMAIL_FROM:-honestbackup@$(hostname)}"

# Email body: summary + tail of log
BODY=$(cat <<EOF
HonestBackup run completed.

Status: $STATUS
Timestamp: $(date)
Log file: $LOG_FILE

--- Last 20 lines of log ---
$(tail -20 "$LOG_FILE")
EOF
)

# Send email if mail command is available
if command -v mail >/dev/null 2>&1; then
    echo "$BODY" | mail -s "$STATUS" -a "From: $FROM" "$TO"
elif command -v mailx >/dev/null 2>&1; then
    echo "$BODY" | mailx -s "$STATUS" -r "$FROM" "$TO"
else
    echo "Warning: No mail command found. Skipping email notification."
    echo "To enable email notifications, install mailutils or mailx and configure your system's MTA."
    echo "Email would have been sent to: $TO"
    echo "Subject: $SUBJECT"
    echo "Body:"
    echo "$BODY"
fi

exit $RESULT