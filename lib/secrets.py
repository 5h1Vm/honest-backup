from pathlib import Path
import subprocess
import os
import sys

PROJECT_ROOT = Path(__file__).resolve().parents[1]
CONFIG_FILE = PROJECT_ROOT / "config" / "backup.conf"


def get_config():
    cfg = {}

    if CONFIG_FILE.exists():
        with open(CONFIG_FILE) as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                if "=" not in line:
                    continue
                key, value = line.split("=", 1)
                cfg[key.strip()] = value.strip()

    return cfg


def load_env():
    # Load .env file if present
    env_path = PROJECT_ROOT / ".env"
    if env_path.is_file():
        try:
            with open(env_path) as f:
                for line in f:
                    line = line.strip()
                    if not line or line.startswith('#'):
                        continue
                    if '=' in line:
                        key, value = line.split('=', 1)
                        key = key.strip()
                        value = value.strip()
                        # Remove surrounding quotes if present
                        if (value.startswith('"') and value.endswith('"')) or \
                           (value.startswith("'") and value.endswith("'")):
                            value = value[1:-1]
                        # Only set if not already in environment (do not override)
                        if key not in os.environ:
                            os.environ[key] = value
        except Exception:
            # If .env cannot be read, continue silently; you could log if desired
            pass

    # We are using KeePass as the secret provider (only supported now)
    # We have a built-in list of secret variable names that are expected as entries in KeePass.
    known_secrets = [
        "TENANT_ID",
        "CLIENT_ID",
        "CLIENT_SECRET",
        "CLOUDFLARE_API_TOKEN",
        "ZONE_ID",
        "CLOUDFLARE_ACCOUNT_ID",
        "NOTION_TOKEN",
        "B2_KEY_ID",
        "B2_APPLICATION_KEY",
        "EMAIL_USERNAME",
        "EMAIL_PASSWORD",
        "WEBHOOK_HMAC_SECRET",
        "SLACK_WEBHOOK_URL",
        "TEAMS_WEBHOOK_URL",
        "PAGERDUTY_INTEGRATION_KEY",
        "TELEGRAM_BOT_TOKEN",
    ]

    # Get KeePass database and password from environment
    kp_db = os.environ.get("KEEPASS_DATABASE")
    kp_pass = os.environ.get("KEEPASS_PASSWORD")
    if not kp_db:
        raise ValueError("KEEPASS_DATABASE environment variable must be set for KeePass provider")
    if not kp_pass:
        raise ValueError("KEEPASS_PASSWORD environment variable must be set for KeePass provider")

    # A database that will not open is fatal — nothing can run without its
    # credentials, and every lookup after this would fail for the same
    # reason while blaming whichever entry happened to be asked for first.
    # Checked once, here, so the error names the real problem.
    probe = subprocess.run(
        ["keepassxc-cli", "ls", "--quiet", kp_db],
        input=kp_pass, text=True, capture_output=True,
    )
    if probe.returncode != 0:
        raise RuntimeError(
            f"Could not open the credential database {kp_db}: "
            f"{probe.stderr.strip() or 'wrong password?'}"
        )

    env = {}
    for var_name in known_secrets:
        # Use keepassxc-cli to get the secret (password field) from the entry
        # The entry title (or UUID) must match the variable name
        cmd = [
            "keepassxc-cli",
            "show",
            "--attributes=password",
            "--show-protected",
            "--quiet",
            kp_db,
            var_name
        ]
        try:
            result = subprocess.run(
                cmd,
                input=kp_pass,
                text=True,
                capture_output=True,
                check=True
            )
            secret = result.stdout.strip()
            env[var_name] = secret
        except subprocess.CalledProcessError:
            # An entry that is not there means that credential was never
            # configured, which is ordinary: an installation backing up only
            # Microsoft 365 has no Cloudflare token and no Notion token, and
            # a site with no Telegram has no bot. Treating any one of those
            # as fatal meant such an installation could not start at all,
            # and the message named a service nobody had asked for.
            #
            # The collectors already report an absent credential as "not
            # configured" and carry on, so leaving it out of the map is
            # enough. The database itself was proved openable above, so
            # this really is a missing entry and not a wider failure.
            continue
    return env
