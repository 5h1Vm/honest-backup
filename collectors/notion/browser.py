from pathlib import Path

from playwright.sync_api import sync_playwright

from .config import (
    WORKSPACE_URL,
    PROFILE_DIR as CONFIG_PROFILE_DIR,
    EXPORT_TIMEOUT_MINUTES,
    BROWSER_EXECUTABLE,
)
import os

EXPORT_TIMEOUT_MS = EXPORT_TIMEOUT_MINUTES * 60 * 1000
STEP_WAIT_MS = 1500


class NotionBrowser:
    def __init__(self, logger):
        self.logger = logger

    def _wait(self, page, ms=STEP_WAIT_MS):
        page.wait_for_timeout(ms)

    def _launch(self):
        playwright = sync_playwright().start()

        # Use environment variable if set, otherwise fall back to config
        profile_dir = os.environ.get("NOTION_PROFILE_DIR", CONFIG_PROFILE_DIR)
        context = playwright.chromium.launch_persistent_context(
            user_data_dir=profile_dir,
            headless=True,
            accept_downloads=True,
            executable_path=BROWSER_EXECUTABLE,
            args=[
                "--no-sandbox",
                "--disable-dev-shm-usage",
                "--disable-gpu",
                "--no-zygote",
                "--single-process",
                "--disable-features=VizDisplayCompositor",
            ],
        )
        if context.pages:
            page = context.pages[0]

        else:
            page = context.new_page()

        page.set_default_timeout(60000)
        page.set_default_navigation_timeout(60000)
        return playwright, context, page

    def _open_export_dialog(self, page):
        self.logger.info("Opening workspace")
        page.goto(
            WORKSPACE_URL,
            wait_until="domcontentloaded",
            timeout=60000,
        )

        # Then explicitly wait for the UI element you actually need
        page.locator('[aria-label="Actions"]').wait_for(
            state="visible",
            timeout=60000,
        )
        page.wait_for_load_state("load")  # add this line
        actions = page.get_by_label("Actions")
        actions.wait_for(state="visible", timeout=60000)
        self.logger.info("Opening actions menu")
        actions.scroll_into_view_if_needed()
        actions.click(force=True)
        page.wait_for_timeout(1500)
        export_item = page.get_by_role("menuitem", name="Export")
        if export_item.count() == 0:
            export_item = page.locator("text=Export").last

        try:
            export_item.wait_for(state="visible", timeout=30000)
        except Exception:
            page.screenshot(path="notion_failure.png", full_page=True)
            self.logger.error("Saved screenshot: notion_failure.png")
            raise

        self.logger.info("Opening export dialog")
        export_item.click()
        dialog = page.locator('[role="dialog"]').last
        dialog.wait_for(state="visible", timeout=30000)
        self._wait(page, 1500)

    def _select_export_format(self, page, export_format):
        dialog = page.locator('[role="dialog"]').last
        self.logger.info(f"Selecting format: {export_format}")
        selector = dialog.locator('[role="button"]').filter(has_text="HTML")
        if selector.count() == 0:
            selector = dialog.locator('[role="button"]').filter(has_text="Markdown")

        if selector.count() == 0:
            selector = dialog.locator('[role="button"]').filter(has_text="PDF")

        if selector.count() == 0:
            raise RuntimeError("Export format selector missing")

        selector.first.click()
        self._wait(page, 2000)
        option = page.get_by_role("menuitem", name=export_format)
        if option.count() == 0:
            option = page.locator('[role="option"]').filter(has_text=export_format)

        if option.count() == 0:
            raise RuntimeError(f"Format option not found: {export_format}")

        option.first.click()
        self._wait(page, 2000)
        self.logger.success(f"Selected format: {export_format}")

    def _select_default_view(self, page):
        dialog = page.locator('[role="dialog"]').last
        try:
            current = dialog.locator('[role="button"]').filter(has_text="Current view")
            if current.count() == 0:
                return

            self.logger.info("Selecting default view")
            current.first.click()
            default = page.get_by_role("menuitem", name="Default view")
            if default.count():
                default.first.click()
                self.logger.success("Default view selected")

        except Exception as e:
            self.logger.warning(f"Default view skipped: {e}")

    def _enable_subpages(self, page):
        self.logger.info("Enabling export switches")
        switches = page.locator('input[type="checkbox"][role="switch"]')
        count = switches.count()
        self.logger.info(f"Found {count} switches")
        if count == 0:
            self.logger.warning("No switches found")
            return

        # run twice because second switch becomes enabled
        # only after first switch is turned on
        for round_no in range(2):
            self.logger.info(f"Switch pass {round_no + 1}")
            count = switches.count()
            for i in range(count):
                switch = switches.nth(i)
                try:
                    if switch.is_disabled():
                        self.logger.info(f"Switch {i+1} disabled")
                        continue

                    checked = switch.is_checked()
                    if not checked:
                        self.logger.info(f"Turning ON switch {i+1}")
                        switch.click(force=True)
                        self._wait(page, 1000)
                        if switch.is_checked():
                            self.logger.success(f"Switch {i+1} enabled")

                except Exception as e:
                    self.logger.warning(f"Switch {i+1} error: {e}")

        self.logger.success("All available switches processed")

    def _configure_export(self, page, export_format):
        self.logger.info(f"Configuring {export_format}")
        self._select_export_format(page, export_format)
        self._select_default_view(page)
        self._enable_subpages(page)
        self._wait(page, 2000)
        self.logger.success("Export configuration complete")

    def _download_export(self, page, download_dir, export_format):
        dialog = page.locator('[role="dialog"]').last
        button = dialog.locator('[role="button"]').filter(has_text="Export")
        if button.count() == 0:
            raise RuntimeError("Export button not found")

        self.logger.info("Starting export")
        with page.expect_download(timeout=EXPORT_TIMEOUT_MS) as download_info:
            button.first.wait_for(state="visible", timeout=60000)
            button.first.click()

        download = download_info.value
        download_dir = Path(download_dir)
        download_dir.mkdir(parents=True, exist_ok=True)
        if export_format == "Markdown & CSV":
            filename = "markdown_export.zip"

        elif export_format == "HTML":
            filename = "html_export.zip"

        else:
            filename = download.suggested_filename

        target = download_dir / filename
        download.save_as(str(target))
        self.logger.success(f"Downloaded {filename}")
        return target

    def _run_export(self, export_format, download_dir):
        playwright = None
        context = None
        try:
            playwright, context, page = self._launch()
            if page.is_closed():
                raise RuntimeError("Browser page closed immediately after launch.")

            self._open_export_dialog(page)
            self._configure_export(page, export_format)
            return self._download_export(page, download_dir, export_format)

        finally:
            if context:
                context.close()

            if playwright:
                playwright.stop()

    def export_markdown_csv(self, download_dir):
        return self._run_export("Markdown & CSV", download_dir)

    def export_html(self, download_dir):
        return self._run_export("HTML", download_dir)

    def export_workspace(self, download_dir):
        markdown = self._run_export("Markdown & CSV", download_dir)
        html = self._run_export("HTML", download_dir)
        return {
            "markdown_csv": str(markdown),
            "html": str(html),
        }
