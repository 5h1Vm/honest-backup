from notion_client import Client

from lib.secrets import load_env


class NotionAPI:

    def __init__(self):

        env = load_env()

        self.client = Client(
            auth=env["NOTION_TOKEN"]
        )

    def search(
        self,
        start_cursor=None,
        page_size=100
    ):

        kwargs = {
            "page_size": page_size
        }

        if start_cursor:
            kwargs["start_cursor"] = start_cursor

        return self.client.search(**kwargs)

    def retrieve_page(
        self,
        page_id
    ):

        return self.client.pages.retrieve(
            page_id=page_id
        )

    def retrieve_database(
        self,
        database_id
    ):

        return self.client.databases.retrieve(
            database_id=database_id
        )

    def list_all_objects(self):

        results = []

        cursor = None

        while True:

            response = self.search(
                start_cursor=cursor
            )

            results.extend(
                response["results"]
            )

            if not response["has_more"]:
                break

            cursor = response["next_cursor"]

        return results
