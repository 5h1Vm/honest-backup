#!/bin/bash
# HonestBackup Setup Script
# Installs dependencies and configures the system for headless operation

set -e  # Exit on any error

echo "Setting up HonestBackup..."

# Update package list
echo "Updating package list..."
apt-get update >/dev/null 2>&1

# Install system dependencies
echo "Installing system dependencies..."
apt-get install -y python3-pip >/dev/null 2>&1

# Upgrade pip
echo "Upgrading pip..."
pip3 install --upgrade pip >/dev/null 2>&1

# Install Python dependencies (user install to avoid needing sudo)
echo "Installing Python dependencies..."
pip3 install --user -r requirements.txt >/dev/null 2>&1

# Install Playwright browsers
echo "Installing Playwright browsers..."
python3 -m playwright install --with-deps chromium >/dev/null 2>&1

# Create necessary directories
echo "Creating necessary directories..."
mkdir -p workspace logs state/m365 state/notion/profile backups

# Create default config if it doesn't exist
if [ ! -f "config/backup.conf" ]; then
    echo "Creating default configuration..."
    mkdir -p config
    cp config/backup.conf.example config/backup.conf

    # Update config for headless operation
    # Note: We use the Chromium browser installed by Playwright in the user's cache
    # The path is typically: $HOME/.cache/ms-playwright/chromium-*/chrome-linux64/chrome
    # We'll use a Python one-liner to find it, but if not found, we fall back to a default.
    BROWSER_PATH=$(python3 -c "import sys, glob, os;
        paths = glob.glob(os.path.expanduser('~/.cache/ms-playwright/chromium-*/*/chrome-linux64/chrome'));
        print(paths[0] if paths else '/usr/bin/chromium-browser')" 2>/dev/null || echo '/usr/bin/chromium-browser')

    sed -i "s|NOTION_BROWSER_EXECUTABLE=.*|NOTION_BROWSER_EXECUTABLE=${BROWSER_PATH}|" config/backup.conf

    # Set profile directory to a relative path
    sed -i 's|NOTION_PROFILE_DIR=.*|NOTION_PROFILE_DIR=state/notion/profile|' config/backup.conf
fi

# Create secrets directory and placeholder if needed
mkdir -p config/keys config/runtime
if [ ! -f "config/secrets.env.age" ]; then
    echo "Creating placeholder secrets file..."
    echo "# Add your encrypted secrets here using age encryption" > config/secrets.env.age
    echo "# See README.md for instructions on setting up encryption" >> config/secrets.env.age
fi

echo ""
echo "Setup complete!"
echo ""
echo "Next steps:"
echo "1. Install Playwright dependencies if needed: sudo playwright install-deps"
echo "2. Configure your secrets in config/secrets.env.age (see README.md)"
echo "3. Run a test backup: python3 -m orchestrator.run --force"
echo ""
echo "Note: The Notion collector is now configured to run in headless mode"
echo "using the Chromium browser bundled with Playwright."