"""Offline contract tests: every HTTP transport is blocked, all data is synthetic."""
import copy
import os
import unittest
from unittest.mock import MagicMock, patch

with patch("dotenv.load_dotenv"):
    from weread2notion import cli


SCHEMA = {
    "书名": "title", "BookId": "rich_text", "Sort": "number",
    "阅读状态": "status", "最后阅读时间": "date", "书架分类": "select",
    "作者": "rich_text", "ISBN": "rich_text", "链接": "url",
    "评分": "number", "分类": "multi_select", "阅读时长": "rich_text",
    "阅读进度": "number", "时间": "date",
}
STAMP = 1700000000
ISO = "2023-11-14T22:13:20+00:00"


def make_page(book_id="111", sort=5):
    return {"id": "page-" + book_id, "properties": {
        "BookId": {"type": "rich_text", "rich_text": [{"plain_text": book_id}]},
        "Sort": {"type": "number", "number": sort},
        "阅读状态": {"type": "status", "status": {"name": "想读"}},
        "最后阅读时间": {"type": "date", "date": None},
        "书架分类": {"type": "select", "select": {"name": "not now"}},
    }}


class ReadingPropertiesTests(unittest.TestCase):
    def setUp(self):
        # Block both SDK and raw HTTP, and sockets as a final guard.
        for target in (
            "requests.sessions.Session.request", "httpx.Client.send",
            "socket.create_connection", "socket.socket.connect",
        ):
            guard = patch(target, side_effect=AssertionError("Network forbidden in tests"))
            guard.start()
            self.addCleanup(guard.stop)
        env = patch.dict(os.environ, {}, clear=True)
        env.start()
        self.addCleanup(env.stop)
        for name in ("client", "weread", "data_source_id", "title_property_name",
                     "data_source_property_types", "skipped_property_names"):
            saved = patch.object(cli, name, getattr(cli, name))
            saved.start()
            self.addCleanup(saved.stop)
        cli.client = MagicMock()
        cli.weread = MagicMock()
        cli.data_source_id = "fake-source"
        cli.title_property_name = "书名"
        cli.data_source_property_types = dict(SCHEMA)
        cli.skipped_property_names = set()

    def progress_response(self, book):
        cli.weread.request.return_value = {"book": book}

    def test_official_status_contract_and_invalid_progress(self):
        cases = [
            ({"progress": 0}, "想读"),
            ({"progress": 0, "isStartReading": True}, "在读"),
            ({"progress": 0, "isStartReading": "1"}, "在读"),
            ({"progress": 0, "updateTime": STAMP}, "想读"),
            ({"progress": 1}, "在读"), ({"progress": 99}, "在读"),
            ({"progress": "100"}, "已读"),
            ({"progress": 20, "finishTime": STAMP}, "在读"),
            ({}, None), ({"progress": -1}, None),
            ({"progress": 101}, None), ({"progress": 0.5}, None),
            ({"progress": True}, None), ({"progress": "bad"}, None),
            ({"progress": "nan"}, None), ({"progress": "inf"}, None),
            ({"progress": 0, "isStartReading": "bad"}, None),
        ]
        for book, expected in cases:
            with self.subTest(book=book):
                self.assertEqual(cli.get_reading_status(book), expected)

    def test_unix_seconds_and_invalid_timestamps(self):
        self.assertEqual(cli.reading_timestamp_to_iso(STAMP), ISO)
        self.assertEqual(cli.reading_timestamp_to_iso(str(STAMP)), ISO)
        for value in (None, 0, -1, True, "bad", "nan", "inf", STAMP * 1000):
            with self.subTest(value=value):
                self.assertIsNone(cli.reading_timestamp_to_iso(value))

    def test_refresh_changes_only_two_properties_without_touching_contents(self):
        page = make_page()
        original = copy.deepcopy(page)
        cli.client.request.return_value = {"results": [page], "has_more": False}
        self.progress_response({"progress": 1, "updateTime": STAMP})
        cli.refresh_reading_properties("111")
        cli.client.pages.update.assert_called_once_with(
            page_id="page-111", properties={
                "阅读状态": {"status": {"name": "在读"}},
                "最后阅读时间": {"date": {"start": ISO, "end": None, "time_zone": None}},
            })
        self.assertEqual(page, original)
        cli.client.pages.create.assert_not_called()
        cli.client.blocks.delete.assert_not_called()
        cli.client.blocks.children.append.assert_not_called()
        cli.weread.request.assert_called_once_with("/book/getprogress", bookId="111")
        self.assertEqual(cli.client.request.call_args.kwargs["body"], {
            "filter": {"property": "BookId", "rich_text": {"equals": "111"}},
            "page_size": 2,
        })

    def test_unchanged_dates_compare_instants_across_timezones(self):
        for date in (
            {"start": "2023-11-14T22:13:20Z", "end": None},
            {"start": "2023-11-15T06:13:20+08:00", "end": None},
            {"start": "2023-11-15T06:13:20", "time_zone": "Asia/Shanghai"},
        ):
            with self.subTest(date=date):
                page = make_page()
                page["properties"]["阅读状态"]["status"]["name"] = "在读"
                page["properties"]["最后阅读时间"]["date"] = date
                cli.client.request.return_value = {"results": [page]}
                self.progress_response({"progress": 20, "updateTime": STAMP})
                cli.refresh_reading_properties("111")
        cli.client.pages.update.assert_not_called()

    def test_only_changed_property_is_sent_and_invalid_values_preserve_old(self):
        page = make_page()
        cli.client.request.return_value = {"results": [page]}
        self.progress_response({"progress": 100, "updateTime": 0})
        cli.refresh_reading_properties("111")
        cli.client.pages.update.assert_called_once_with(
            page_id="page-111", properties={"阅读状态": {"status": {"name": "已读"}}})
        cli.client.pages.update.reset_mock()
        self.progress_response({"progress": "bad", "updateTime": STAMP})
        cli.refresh_reading_properties("111")
        self.assertEqual(set(cli.client.pages.update.call_args.kwargs["properties"]),
                         {"最后阅读时间"})
        cli.client.pages.update.reset_mock()
        self.progress_response({})
        cli.refresh_reading_properties("111")
        cli.client.pages.update.assert_not_called()

    def test_missing_or_duplicate_matches_are_skipped_without_progress_request(self):
        for response in (
            {"results": []},
            {"results": [make_page(), make_page()], "has_more": False},
            {"results": [make_page()], "has_more": True},
        ):
            with self.subTest(response=response):
                cli.client.request.return_value = response
                cli.refresh_reading_properties("111")
        cli.weread.request.assert_not_called()
        cli.client.pages.update.assert_not_called()
        cli.client.pages.create.assert_not_called()

    def test_missing_or_wrong_schema_does_not_write_or_query(self):
        for schema in ({}, {"阅读状态": "select", "最后阅读时间": "rich_text"}):
            cli.data_source_property_types = schema
            cli.refresh_reading_properties("111")
        cli.client.request.assert_not_called()
        cli.weread.request.assert_not_called()
        cli.client.pages.update.assert_not_called()

    def test_progress_failure_preserves_existing_properties(self):
        cli.client.request.return_value = {"results": [make_page()]}
        with patch.object(cli, "get_read_info", side_effect=RuntimeError("synthetic failure")):
            cli.refresh_reading_properties("111")
        cli.client.pages.update.assert_not_called()

    def install_sync_fakes(self, pages, notebooks, progress):
        """Exercise the real sync, create, refresh, chapter and note functions."""
        notion, gateway = cli.client, cli.weread

        def notion_request(path, method, body=None):
            if method == "GET":
                return {"properties": {key: {"type": value} for key, value in SCHEMA.items()}}
            if body.get("sorts"):
                values = list(pages.values())
                return {"results": sorted(values, key=lambda p: p["properties"]["Sort"]["number"],
                                          reverse=True)[:1]}
            book_id = body["filter"]["rich_text"]["equals"]
            return {"results": [copy.deepcopy(pages[book_id])] if book_id in pages else [],
                    "has_more": False}

        def create_page(**kwargs):
            props = copy.deepcopy(kwargs["properties"])
            book_id = props["BookId"]["rich_text"][0]["text"]["content"]
            pages[book_id] = {"id": "created-" + book_id, "properties": {
                name: {"type": next(iter(value)), **value} for name, value in props.items()}}
            return {"id": "created-" + book_id}

        def update_page(page_id, properties):
            for page in pages.values():
                if page["id"] == page_id:
                    page["properties"].update(copy.deepcopy(properties))
                    return {"id": page_id}
            raise AssertionError("Unknown page")

        def gateway_request(api_name, **kwargs):
            if api_name == "/user/notebooks":
                return {"books": notebooks, "hasMore": 0}
            if api_name == "/book/getprogress":
                return {"book": progress[kwargs["bookId"]]}
            if api_name == "/book/info":
                return {"isbn": "synthetic-isbn", "newRating": 85}
            if api_name == "/book/chapterinfo":
                return {"chapters": [{"chapterUid": 1, "chapterIdx": 1,
                                      "level": 1, "title": "Synthetic chapter"}]}
            if api_name == "/book/bookmarklist":
                return {"updated": [{"chapterUid": 1, "range": "1-2", "markText": "Highlight"}]}
            if api_name == "/review/list/mine":
                return {"reviews": [
                    {"review": {"type": 1, "chapterUid": 1, "range": "3-4",
                                "content": "Note", "abstract": "Quote"}},
                    {"review": {"type": 4, "content": "Summary"}},
                ], "hasMore": 0}
            raise AssertionError("Unexpected gateway endpoint: " + api_name)

        notion.request.side_effect = notion_request
        notion.pages.create.side_effect = create_page
        notion.pages.update.side_effect = update_page
        notion.blocks.children.append.side_effect = lambda block_id, children: {
            "results": [{"id": "block-" + str(i)} for i in range(len(children))]}
        gateway.request.side_effect = gateway_request
        replacements = {
            "validate_secret_inputs": lambda: {"notion_token": "fake", "weread_api_key": "fake"},
            "extract_notion_id": lambda: "fake-source",
            "resolve_data_source_id": lambda value: value,
            "Client": lambda **kwargs: notion,
            "WeReadGatewayClient": lambda value: gateway,
        }
        for name, replacement in replacements.items():
            mocked = patch.object(cli, name, replacement)
            mocked.start()
            self.addCleanup(mocked.stop)
        sleep = patch.object(cli.time, "sleep")
        sleep.start()
        self.addCleanup(sleep.stop)
        return notion, gateway

    def test_full_sync_existing_book_new_book_and_unchanged_second_run(self):
        pages = {"111": make_page()}
        original = copy.deepcopy(pages["111"])
        notebooks = [{"sort": 5, "book": {"bookId": "111", "title": "Existing"}},
                     {"sort": 11, "book": {"bookId": "222", "title": "New", "author": "Author"}}]
        progress = {"111": {"progress": 1, "updateTime": STAMP},
                    "222": {"progress": 100, "finishTime": STAMP, "updateTime": STAMP + 1,
                            "recordReadingTime": 3660}}
        notion, gateway = self.install_sync_fakes(pages, notebooks, progress)
        cli.sync()
        notion.pages.update.assert_called_once()
        notion.pages.create.assert_called_once()
        notion.blocks.delete.assert_not_called()
        for name, value in original["properties"].items():
            if name not in ("阅读状态", "最后阅读时间"):
                self.assertEqual(pages["111"]["properties"][name], value)
        new_props = notion.pages.create.call_args.kwargs["properties"]
        self.assertEqual(new_props["阅读状态"], {"status": {"name": "已读"}})
        self.assertEqual(new_props["最后阅读时间"]["date"]["start"], "2023-11-14T22:13:21+00:00")
        self.assertNotIn("状态", new_props)
        self.assertEqual(new_props["作者"]["rich_text"][0]["text"]["content"], "Author")
        self.assertEqual(new_props["阅读时长"]["rich_text"][0]["text"]["content"], "1时1分")
        self.assertEqual(new_props["阅读进度"], {"number": 1.0})
        appended = notion.blocks.children.append.call_args_list
        self.assertEqual([block["type"] for block in appended[0].kwargs["children"]],
                         ["heading_1", "callout", "callout", "heading_1", "callout"])
        self.assertEqual(appended[1].kwargs["children"][0]["type"], "quote")
        for book_id in ("111", "222"):
            self.assertEqual(sum(c.args == ("/book/getprogress",) and c.kwargs == {"bookId": book_id}
                                 for c in gateway.request.call_args_list), 1)
        notion.pages.update.reset_mock()
        notion.pages.create.reset_mock()
        notion.blocks.children.append.reset_mock()
        gateway.request.reset_mock()
        cli.sync()
        notion.pages.update.assert_not_called()
        notion.pages.create.assert_not_called()
        notion.blocks.delete.assert_not_called()
        notion.blocks.children.append.assert_not_called()
        self.assertEqual(len(gateway.request.call_args_list), 3)  # notebooks + 2 progress reads

    def test_original_sort_triggered_rebuild_remains_single_path(self):
        pages = {"111": make_page(sort=5)}
        notebooks = [{"sort": 11, "bookId": "111", "title": "Updated notes"}]
        notion, gateway = self.install_sync_fakes(
            pages, notebooks, {"111": {"progress": 99, "updateTime": STAMP}})
        with patch.object(cli, "refresh_reading_properties") as refresh:
            cli.sync()
        refresh.assert_not_called()
        notion.blocks.delete.assert_called_once_with(block_id="page-111")
        notion.pages.create.assert_called_once()
        notion.pages.update.assert_not_called()
        self.assertEqual(sum(c.args == ("/book/getprogress",)
                             for c in gateway.request.call_args_list), 1)
        self.assertEqual(len(notion.blocks.children.append.call_args_list), 2)

    def test_duplicate_notebook_ids_do_not_cross_refresh_and_original_paths(self):
        pages = {"111": make_page(sort=5)}
        notebooks = [{"sort": 4, "book": {"bookId": "111"}},
                     {"sort": 11, "bookId": "111", "title": "Changed"}]
        notion, gateway = self.install_sync_fakes(
            pages, notebooks, {"111": {"progress": 1, "updateTime": STAMP}})
        with patch.object(cli, "refresh_reading_properties") as refresh:
            cli.sync()
        refresh.assert_not_called()
        notion.pages.update.assert_not_called()
        notion.pages.create.assert_called_once()
        self.assertEqual(sum(c.args == ("/book/getprogress",)
                             for c in gateway.request.call_args_list), 1)

    def test_duplicate_sort_skipped_ids_are_refreshed_once(self):
        pages = {"111": make_page(sort=5)}
        notebooks = [{"sort": 4, "book": {"bookId": "111"}},
                     {"sort": 5, "bookId": "111", "title": "Existing"}]
        notion, gateway = self.install_sync_fakes(
            pages, notebooks, {"111": {"progress": 1, "updateTime": STAMP}})
        cli.sync()
        notion.pages.update.assert_called_once()
        notion.pages.create.assert_not_called()
        notion.blocks.delete.assert_not_called()
        self.assertEqual(sum(c.args == ("/book/getprogress",)
                             for c in gateway.request.call_args_list), 1)


if __name__ == "__main__":
    unittest.main()
