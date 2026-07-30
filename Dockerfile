# HonestBackup in a container.
#
# Built on Microsoft's Playwright image because Notion's export drives a real
# Chromium, and the alternative is chasing the sixty-odd shared libraries
# headless Chrome wants on a bare Debian. The tag must match the playwright
# pin in requirements.txt: Playwright refuses to drive a browser built for a
# different version of itself, and the error when it does is not obvious.
FROM mcr.microsoft.com/playwright/python:v1.61.0-noble

# age, zstd and rclone are the archive pipeline; keepassxc-cli reads the
# credential database. None are pip packages. tar and cron come with the base.
#
# keepassxc-cli pulls a lot of Qt on Ubuntu even for the CLI, which is most of
# this layer's size. It is still the right call: the alternative is a second
# secret store for containers, and one credential path is worth more than a
# smaller image.
RUN apt-get update && apt-get install -y --no-install-recommends \
        age \
        zstd \
        rclone \
        keepassxc \
        ca-certificates \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Requirements first, so a code change does not reinstall the dependency tree.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Everything that has to outlive the container. A container that loses these
# is not a slower backup, it is a different one: without workspace/ there is
# nothing to carry forward and every file is fetched again, without state/
# every collector restarts from the beginning of time, and without the Notion
# profile the export has no logged-in session to export from.
VOLUME ["/app/workspace", "/app/backupvault", "/app/state", \
        "/app/config/keys", "/app/honestbackup-profile"]

# Left empty so Playwright uses the Chromium it shipped with. Naming a path
# here would pin a version that changes every time the image is rebuilt.
ENV NOTION_BROWSER_EXECUTABLE=""
ENV NOTION_PROFILE_DIR=/app/honestbackup-profile
ENV PYTHONUNBUFFERED=1

# The image runs one backup and exits, which is what a scheduled job wants —
# Container Apps Jobs, Kubernetes CronJob and `docker run` on a timer all
# expect the process to finish. The container is not the scheduler; use the
# platform's own, and delete the crontab this project writes for a VM.
ENTRYPOINT ["python3", "-m", "orchestrator.run"]
CMD ["--incremental", "--only", "cloudflare,m365,notion"]
