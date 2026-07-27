from pathlib import Path
import json

from .api_client import NotionAPI


class DataSourceExporter:

    def __init__(self, output_dir):

        self.output_dir = Path(output_dir)

        self.output_dir.mkdir(
            parents=True,
            exist_ok=True
        )

        self.api = NotionAPI()


    def _write(self, filename, data):

        outfile = self.output_dir / filename


        with open(
            outfile,
            "w",
            encoding="utf-8"
        ) as f:

            json.dump(
                data,
                f,
                indent=2,
                ensure_ascii=False
            )


        print(f"[+] Wrote {outfile}")

        return outfile



    def export(self):

        objects = self.api.list_all_objects()


        data_sources = []


        for obj in objects:

            if obj["object"] in (
                "database",
                "data_source"
            ):

                data_sources.append(obj)



        print()
        print("========================================")
        print("Exporting Data Sources")
        print("========================================")


        print(
            f"[+] Found {len(data_sources)} data sources"
        )


        exported = []


        for ds in data_sources:

            dsid = ds["id"]


            print(
                f"[+] Retrieving {dsid}"
            )


            details = self.api.client.data_sources.retrieve(
                data_source_id=dsid
            )


            exported.append(
                details
            )



        self._write(
            "data_sources_full.json",
            exported
        )


        return len(exported)