from pathlib import Path
from datetime import datetime
import json

from collectors.notion.api_client import NotionAPI


class StatisticsCollector:

    def __init__(self, output_dir: Path):

        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(
            parents=True,
            exist_ok=True
        )

        self.api = NotionAPI()

    def collect(self):

        print()
        print("=" * 60)
        print("Notion Statistics")
        print("=" * 60)

        objects = self.api.list_all_objects()
        users = self.api.client.users.list()["results"]

        stats = {
            "generated_at": datetime.utcnow().isoformat() + "Z",
            "object_count": len(objects),
            "page_count": 0,
            "database_count": 0,
            "data_source_count": 0,
            "user_count": len(users),
            "archived_pages": 0,
            "trashed_pages": 0
        }

        for obj in objects:

            if obj["object"] == "page":

                stats["page_count"] += 1

                if obj.get("archived", False):
                    stats["archived_pages"] += 1

                if obj.get("in_trash", False):
                    stats["trashed_pages"] += 1

            elif obj["object"] == "database":

                stats["database_count"] += 1

            elif obj["object"] == "data_source":

                stats["data_source_count"] += 1

        outfile = self.output_dir / "statistics.json"

        with open(outfile, "w") as fp:
            json.dump(
                stats,
                fp,
                indent=2
            )

        print(json.dumps(stats, indent=2))
        print()
        print(f"[+] Wrote {outfile}")

        return outfile
