"""Notion collection.

Two independent halves:

  Browser export — drives a real browser to trigger Notion's own workspace
  export (Markdown & CSV, HTML & CSV). This is the only way to get full page
  content, and it is slow and fragile because it depends on Notion's UI.

  API collection — inventory, metadata, database schemas, rows and statistics
  through the official Notion API. Fast and reliable.

Each stage runs independently. A browser timeout used to abort the entire
collector, silently skipping all six API stages while still logging
"collection complete" — so a half-finished run looked identical to a good one.
Now every stage is recorded separately and the final status reflects what
actually happened.
"""

from pathlib import Path

from .browser import NotionBrowser
from .api_inventory import build_inventory
from .api import NotionMetadataCollector
from .data_source_export import DataSourceExporter
from .data_source_rows import DataSourceRowsExporter
from .statistics import StatisticsCollector
from .manifest import ManifestBuilder


def collect(workspace, logger):

    workspace = Path(workspace)
    workspace.mkdir(parents=True, exist_ok=True)

    stats = {
        "status": "success",
        "items": {},
        "warnings": [],
        "errors": [],
        "stages": {},
    }

    browser_exports = workspace / "browser"
    api_dir = workspace / "api"

    browser_exports.mkdir(parents=True, exist_ok=True)
    api_dir.mkdir(parents=True, exist_ok=True)

    def stage(name, fn, required_for=None):
        """Run one stage. Returns its result, or None if it failed.

        required_for names the stages that cannot run without this one, so the
        log explains the knock-on effect instead of going quiet.
        """
        logger.info(name)
        try:
            result = fn()
            stats["stages"][name] = "ok"
            logger.success(f"{name} complete")
            return result
        except Exception as e:
            stats["stages"][name] = f"failed: {e}"
            stats["errors"].append(f"{name}: {e}")
            logger.error(f"{name} failed: {e}")
            if required_for:
                skipped = ", ".join(required_for)
                logger.warning(f"  -> skipping dependent stages: {skipped}")
                for dependent in required_for:
                    stats["stages"][dependent] = "skipped (dependency failed)"
            return None

    # ------------------------------------------------------------------
    # Browser export — independent of everything below it
    # ------------------------------------------------------------------
    browser_count = stage(
        "Browser Exports",
        lambda: NotionBrowser(logger).export_workspace(browser_exports),
    )

    if browser_count is not None:
        stats["items"]["browser_exports"] = browser_count or 1
    else:
        # Partial exports are still worth keeping — record what landed.
        downloaded = list(browser_exports.glob("*.zip"))
        if downloaded:
            stats["items"]["browser_exports"] = len(downloaded)
            stats["warnings"].append(
                f"Browser export incomplete — kept {len(downloaded)} file(s): "
                + ", ".join(f.name for f in downloaded)
            )
            logger.warning(
                f"  -> kept {len(downloaded)} partial export file(s)"
            )

    # ------------------------------------------------------------------
    # API collection — runs regardless of how the browser export went
    # ------------------------------------------------------------------
    inventory_ok = stage(
        "Building Inventory",
        lambda: build_inventory(browser_exports, api_dir),
        required_for=["Exporting Data Sources", "Exporting Rows"],
    )

    metadata = stage(
        "Collecting Metadata",
        lambda: NotionMetadataCollector(api_dir).collect_all(),
    )
    if metadata:
        stats["items"].update(metadata)

    if inventory_ok is not None:
        exported = stage(
            "Exporting Data Sources",
            lambda: DataSourceExporter(api_dir).export(),
        )
        if exported is not None:
            stats["items"]["database_exports"] = exported or 0

        rows = stage(
            "Exporting Rows",
            lambda: DataSourceRowsExporter(api_dir / "rows").export(),
        )
        if rows is not None:
            stats["items"]["database_rows"] = rows or 0

    stage(
        "Collecting Statistics",
        lambda: StatisticsCollector(api_dir).collect(),
    )

    stage(
        "Building Manifest",
        lambda: ManifestBuilder(workspace).build(),
    )

    # ------------------------------------------------------------------
    # Honest final status
    # ------------------------------------------------------------------
    failed = [n for n, s in stats["stages"].items() if s.startswith("failed")]
    skipped = [n for n, s in stats["stages"].items() if s.startswith("skipped")]
    succeeded = [n for n, s in stats["stages"].items() if s == "ok"]

    if not succeeded:
        stats["status"] = "failed"
        logger.error("Notion collection failed — no stage completed")
    elif failed or skipped:
        stats["status"] = "partial"
        logger.warning(
            f"Notion collection partial — {len(succeeded)} succeeded, "
            f"{len(failed)} failed, {len(skipped)} skipped"
        )
        for name in failed:
            logger.warning(f"  failed:  {name} — {stats['stages'][name]}")
        for name in skipped:
            logger.warning(f"  skipped: {name}")
    else:
        stats["status"] = "success"
        logger.success(
            f"Notion collection complete — all {len(succeeded)} stages ok"
        )

    return stats
