from pathlib import Path
import json

from .api_client import NotionAPI


class DataSourceRowsExporter:


    def __init__(self, output_dir):

        self.output_dir = Path(output_dir)

        self.output_dir.mkdir(
            parents=True,
            exist_ok=True
        )

        self.api = NotionAPI()



    def export(self):

        objects = self.api.list_all_objects()


        total_rows = 0



        for obj in objects:


            if obj["object"] not in (
                "database",
                "data_source"
            ):

                continue



            dsid = obj["id"]


            print()
            print("=" * 60)
            print(dsid)



            rows = []


            cursor = None



            while True:


                result = self.api.client.data_sources.query(
                    data_source_id=dsid,
                    start_cursor=cursor
                )


                rows.extend(
                    result["results"]
                )


                if not result["has_more"]:

                    break


                cursor = result["next_cursor"]



            outfile = (
                self.output_dir /
                f"{dsid}.json"
            )



            with open(
                outfile,
                "w",
                encoding="utf-8"
            ) as f:


                json.dump(
                    rows,
                    f,
                    indent=2,
                    ensure_ascii=False
                )



            print(
                f"[+] Rows : {len(rows)}"
            )


            print(
                f"[+] Saved: {outfile}"
            )


            total_rows += len(rows)



        return total_rows