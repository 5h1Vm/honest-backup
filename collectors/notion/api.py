from pathlib import Path
import json

from .api_client import NotionAPI


class NotionMetadataCollector:


    def __init__(self, output_dir: Path):

        self.output_dir = Path(output_dir)

        self.output_dir.mkdir(
            parents=True,
            exist_ok=True
        )

        self.api = NotionAPI()



    def _write(self, filename, data):

        path = (
            self.output_dir /
            filename
        )


        with open(
            path,
            "w",
            encoding="utf-8"
        ) as f:

            json.dump(
                data,
                f,
                indent=2,
                ensure_ascii=False
            )


        return path



    def collect_pages(self):

        pages = []


        for obj in self.api.list_all_objects():

            if obj["object"] == "page":

                pages.append(obj)


        self._write(
            "pages.json",
            pages
        )


        return {
            "pages": len(pages)
        }



    def collect_data_sources(self):

        data_sources = []


        for obj in self.api.list_all_objects():

            if obj["object"] in (
                "database",
                "data_source"
            ):

                data_sources.append(obj)


        self._write(
            "data_sources.json",
            data_sources
        )


        return {
            "data_sources": len(data_sources)
        }



    def collect_users(self):

        users = []

        cursor = None


        while True:

            result = self.api.client.users.list(
                start_cursor=cursor
            )


            users.extend(
                result["results"]
            )


            if not result["has_more"]:

                break


            cursor = result["next_cursor"]



        self._write(
            "users.json",
            users
        )


        return {
            "users": len(users)
        }



    def collect_all(self):

        stats = {}


        stats.update(
            self.collect_pages()
        )


        stats.update(
            self.collect_data_sources()
        )


        stats.update(
            self.collect_users()
        )


        return stats