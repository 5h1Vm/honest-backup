from pathlib import Path
import json

from .api_client import NotionAPI


def build_inventory(export_dir, inventory_dir):

    export_dir = Path(export_dir)
    inventory_dir = Path(inventory_dir)

    export_dir.mkdir(
        parents=True,
        exist_ok=True
    )

    inventory_dir.mkdir(
        parents=True,
        exist_ok=True
    )

    api = NotionAPI()

    objects = api.list_all_objects()

    inventory = {
        "generated_by": "HonestBackup",
        "object_count": len(objects),
        "pages": [],
        "databases": [],
        "unknown": []
    }

    for obj in objects:

        item = {
            "id": obj.get("id"),
            "object": obj.get("object"),
            "url": obj.get("url"),
            "created_time": obj.get("created_time"),
            "last_edited_time": obj.get("last_edited_time"),
            "archived": obj.get("archived", False),
            "in_trash": obj.get("in_trash", False)
        }

        if obj["object"] == "page":

            title = ""

            props = obj.get("properties", {})

            for value in props.values():

                if value.get("type") == "title":

                    title = "".join(
                        x["plain_text"]
                        for x in value["title"]
                    )

                    break

            item["title"] = title

            inventory["pages"].append(item)

        elif obj["object"] == "database":

            title = ""

            if obj.get("title"):

                title = "".join(
                    x["plain_text"]
                    for x in obj["title"]
                )

            item["title"] = title

            inventory["databases"].append(item)

        else:

            inventory["unknown"].append(item)

    output = inventory_dir / "inventory.json"

    with open(
        output,
        "w",
        encoding="utf-8"
    ) as f:

        json.dump(
            inventory,
            f,
            indent=2,
            ensure_ascii=False
        )

    print()

    print("========================================")
    print("Notion Inventory")
    print("========================================")

    print("Pages     :", len(inventory["pages"]))
    print("Databases :", len(inventory["databases"]))
    print("Unknown   :", len(inventory["unknown"]))

    print()

    print(f"Inventory written -> {output}")

    return output
