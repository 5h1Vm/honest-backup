#!/usr/bin/env python3
"""Sign the Notion collector's browser profile into an account.

The collector never logs in. It opens the profile in
NOTION_PROFILE_DIR and expects to find a signed-in session already
sitting there, so when the session expires or the account changes, the
export fails with a browser error that says nothing about credentials.
This is the missing half: it drives the same profile through Notion's
email-and-code sign-in from a terminal, with no display anywhere.

    ./notion-login.py            sign in
    ./notion-login.py --check    is the profile signed in?
    ./notion-login.py --reset    move the profile aside and start over

Notion mails a six-digit code rather than taking a password, so this
asks for the address, waits while you fetch the code from your inbox,
and then finishes. Nothing is stored by this script — the session lands
in the profile directory, which is the only thing the collector reads.
"""

import argparse
import os
import shutil
import sys
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from collectors.notion.config import (  # noqa: E402
    BROWSER_EXECUTABLE,
    PROFILE_DIR as CONFIG_PROFILE_DIR,
    WORKSPACE_URL,
)

BOLD, DIM, RED, GREEN, YELLOW, OFF = (
    "\033[1m", "\033[2m", "\033[31m", "\033[32m", "\033[33m", "\033[0m",
)

# The collector needs this control to open the export dialog. If it is on
# the page we are signed in, and signed into the right workspace — a
# weaker check, like "did we get redirected away from /login", passes
# while the account still cannot see the pages we back up.
WORKSPACE_READY = '[aria-label="Actions"]'

# Same flags the collector launches with, so a session that works here
# works there. Without --no-sandbox Chrome aborts on this host: Ubuntu
# restricts unprivileged user namespaces under AppArmor.
LAUNCH_ARGS = [
    "--no-sandbox",
    "--disable-dev-shm-usage",
    "--disable-gpu",
]


def profile_dir() -> Path:
    return Path(os.environ.get("NOTION_PROFILE_DIR", CONFIG_PROFILE_DIR))


def say(message: str) -> None:
    print(f"  {message}", flush=True)


def ask(prompt: str) -> str:
    try:
        return input(f"  {BOLD}{prompt}{OFF} ").strip()
    except (EOFError, KeyboardInterrupt):
        print()
        sys.exit(1)


def open_profile(playwright, headless=True):
    return playwright.chromium.launch_persistent_context(
        user_data_dir=str(profile_dir()),
        headless=headless,
        accept_downloads=True,
        executable_path=BROWSER_EXECUTABLE,
        args=LAUNCH_ARGS,
    )


def only_page(context):
    """One page, and no leftovers from an interrupted session.

    Killing Chrome with Ctrl-C leaves the profile marked as crashed, and
    the next launch restores every tab that was open. They pile up.
    """
    page = context.pages[0] if context.pages else context.new_page()
    for extra in context.pages[1:]:
        extra.close()
    page.set_default_timeout(60000)
    page.set_default_navigation_timeout(60000)
    return page


def workspace_reachable(page) -> bool:
    page.goto(WORKSPACE_URL, wait_until="domcontentloaded", timeout=60000)
    try:
        page.locator(WORKSPACE_READY).wait_for(state="visible", timeout=30000)
        return True
    except Exception:
        return False


def check() -> int:
    from playwright.sync_api import sync_playwright

    directory = profile_dir()
    if not directory.is_dir():
        say(f"{YELLOW}No profile at {directory}{OFF}")
        say(f"{DIM}Run this without --check to make one.{OFF}")
        return 1

    say(f"{DIM}Opening {directory}{OFF}")
    with sync_playwright() as playwright:
        context = open_profile(playwright)
        try:
            page = only_page(context)
            if workspace_reachable(page):
                say(f"{GREEN}Signed in.{OFF} The workspace opens and the "
                    f"export menu is reachable.")
                return 0
            say(f"{RED}Not signed in{OFF} — or signed into an account that "
                f"cannot see this workspace.")
            say(f"{DIM}Landed on: {page.url}{OFF}")
            return 1
        finally:
            context.close()


def reset() -> int:
    directory = profile_dir()
    if not directory.is_dir():
        say(f"{DIM}Nothing to move — {directory} does not exist.{OFF}")
        return 0
    stamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    aside = directory.with_name(f"{directory.name}.{stamp}")
    shutil.move(str(directory), str(aside))
    say(f"{GREEN}Moved{OFF} the old profile to {aside}")
    say(f"{DIM}Delete it once a real backup run has succeeded.{OFF}")
    return 0


def sign_in() -> int:
    from playwright.sync_api import sync_playwright

    print()
    print(f"{BOLD}Signing the Notion profile in{OFF}")
    say(f"{DIM}profile: {profile_dir()}{OFF}")
    print()

    address = ask("Email address for the Notion account:")
    if not address or "@" not in address:
        say(f"{RED}That is not an email address.{OFF}")
        return 1

    with sync_playwright() as playwright:
        context = open_profile(playwright)
        try:
            page = only_page(context)

            say(f"{DIM}Opening the login page…{OFF}")
            page.goto("https://www.notion.so/login",
                      wait_until="domcontentloaded", timeout=60000)

            email_box = page.locator('input[type="email"]').first
            email_box.wait_for(state="visible", timeout=30000)
            email_box.fill(address)
            email_box.press("Enter")

            say(f"{DIM}Asking Notion to send a code…{OFF}")
            page.wait_for_timeout(4000)

            # The code lands in a field Notion does not mark as an email
            # input, so "the visible text box that is not the address" is
            # a steadier target than any one selector it happens to use.
            code_box = page.locator(
                'input[type="text"]:visible, input[type="tel"]:visible, '
                'input[inputmode="numeric"]:visible'
            ).first
            try:
                code_box.wait_for(state="visible", timeout=30000)
            except Exception:
                say(f"{RED}No code box appeared.{OFF}")
                shot = HERE / "notion-login-stuck.png"
                page.screenshot(path=str(shot), full_page=True)
                say(f"{DIM}Screenshot of what it showed: {shot}{OFF}")
                say(f"{DIM}Landed on: {page.url}{OFF}")
                return 1

            print()
            say(f"Notion has emailed a code to {BOLD}{address}{OFF}.")
            code = ask("Paste the code here:")
            if not code:
                say(f"{RED}No code given.{OFF}")
                return 1

            code_box.fill(code)
            code_box.press("Enter")

            say(f"{DIM}Checking the workspace opens…{OFF}")
            page.wait_for_timeout(6000)

            if workspace_reachable(page):
                print()
                say(f"{GREEN}Signed in.{OFF} The workspace opens and the "
                    f"export menu is reachable.")
                print()
                say(f"{DIM}Now prove it end to end:{OFF}")
                say("python3 -m orchestrator.run --force --only notion")
                return 0

            print()
            say(f"{RED}Signed in, but this account cannot open the "
                f"workspace.{OFF}")
            say(f"{DIM}Landed on: {page.url}{OFF}")
            say(f"{DIM}NOTION_WORKSPACE_URL points at:{OFF}")
            say(f"{DIM}  {WORKSPACE_URL}{OFF}")
            say(f"{DIM}Either the account is not a member, or that setting "
                f"names the old workspace.{OFF}")
            shot = HERE / "notion-login-stuck.png"
            page.screenshot(path=str(shot), full_page=True)
            say(f"{DIM}Screenshot: {shot}{OFF}")
            return 1
        finally:
            context.close()


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Sign the Notion collector's browser profile in.")
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--check", action="store_true",
                       help="report whether the profile is signed in")
    group.add_argument("--reset", action="store_true",
                       help="move the profile aside and start over")
    options = parser.parse_args()

    if options.check:
        return check()
    if options.reset:
        return reset()
    return sign_in()


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print()
        sys.exit(1)
