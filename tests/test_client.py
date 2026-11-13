import json
import os
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from contextlib import closing, redirect_stderr, redirect_stdout
from io import StringIO
from pathlib import Path
from unittest.mock import Mock, patch
from urllib.error import HTTPError

from gamesneverdie import Client, FetchError, NotFoundError, ParseError
from gamesneverdie.cli import main
from gamesneverdie.client import MAX_RESPONSE_BYTES, _SameOriginRedirects, game_slug
from gamesneverdie.parsing import FIELDS, parse_game, parse_search

FIXTURES = Path(__file__).parent / "fixtures"
GAME = (FIXTURES / "game.html").read_text(encoding="utf-8")
SEARCH = (FIXTURES / "search.html").read_text(encoding="utf-8")
STEAM = json.dumps(
    {"players": 7, "review": {"pct": 95}, "price": {"formatted": "¥100"}}
)


class ParsingTests(unittest.TestCase):
    def test_game_metadata_and_scope(self):
        g = parse_game(GAME, "test-game")
        self.assertEqual(g["title"], "Test & Game")
        self.assertEqual(g["developers"], ["Studio A", "Studio B"])
        self.assertEqual(g["platforms"], ["PC", "Steam"])
        self.assertEqual(g["description"], "A fictional game.\n\nSecond paragraph.")
        self.assertEqual(g["ratings"], {"igdb": 89.5, "steam": 95})
        self.assertEqual(g["release_date"], "2023-12-12")
        self.assertEqual(
            g["release_dates"],
            [
                {"date": "2023-12-12", "scope": "JP"},
                {"date": "2019-05-28", "scope": "world"},
            ],
        )
        self.assertEqual(g["updated_at"], "2026-11-12")
        self.assertEqual(g["directors"], [])
        self.assertIsNone(g["status"])
        self.assertEqual(set(g) | {"fetched_at"}, set(FIELDS))

    def test_media_articles_and_related_exclude_sidebar_and_duplicate_views(self):
        g = parse_game(GAME, "test-game")
        self.assertEqual(g["trailers"], ["https://www.youtube.com/watch?v=video-123"])
        self.assertEqual(len(g["screenshots"]), 1)
        self.assertEqual(len(g["articles"]), 1)
        self.assertEqual(g["articles"][0]["source"], "Example")
        self.assertEqual([r["slug"] for r in g["related"]], ["related-game"])

    def test_sparse_game_keeps_unknowns_absent(self):
        g = parse_game(
            '<div id="hero"><h1>Unreleased</h1></div><section id="details"></section>',
            "unreleased",
        )
        self.assertIsNone(g["release_date"])
        self.assertEqual(g["release_dates"], [])
        self.assertEqual(g["ratings"], {"igdb": None, "steam": None})

    def test_search_one_page_without_table_duplicates_or_sidebar(self):
        result = parse_search(SEARCH, "Test", 1)
        self.assertEqual((result["total"], result["total_pages"]), (1234, 45))
        self.assertEqual(
            [g["slug"] for g in result["items"]], ["test-game", "another-game"]
        )
        self.assertIsNone(result["items"][1]["rating"])

    def test_empty_search(self):
        result = parse_search(
            '<main><p class="none">該当するタイトルがありません</p></main>', "none", 1
        )
        self.assertEqual(result["total"], 0)
        self.assertEqual(result["items"], [])

    def test_unexpected_html_fails_instead_of_returning_empty_metadata(self):
        for parse in (lambda h: parse_game(h, "x"), lambda h: parse_search(h, "x", 1)):
            with self.assertRaises(ParseError):
                parse("<title>Just a moment...</title>")


class ClientTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.client = Client(cache_dir=self.directory.name, min_interval=0)

    def test_selected_fields_and_shared_persistent_cache(self):
        with patch.object(self.client, "_read", return_value=GAME) as read:
            first = self.client.get("test-game", fields=["title", "fetched_at"])
            second = Client(cache_dir=self.directory.name)
            with patch.object(
                second, "_read", side_effect=AssertionError("Must use cache")
            ):
                cached = second.get(
                    "https://gamesneverdie.com/ja/games/test-game#details",
                    fields=["title", "fetched_at"],
                )
            self.assertEqual(first, cached)
            self.assertEqual(read.call_count, 1)

    def test_search_limit_does_not_poison_cached_page(self):
        with patch.object(self.client, "_read", return_value=SEARCH) as read:
            self.assertEqual(len(self.client.search("Test", limit=1)["items"]), 1)
            self.assertEqual(len(self.client.search("Test")["items"]), 2)
            read.assert_called_once()

    def test_steam_is_opt_in_and_selection_fetches_it(self):
        with patch.object(self.client, "_read", side_effect=[GAME, STEAM]) as read:
            self.assertIsNone(self.client.get("test-game")["steam"])
            result = self.client.get("test-game", fields=["steam"])
            self.assertEqual(result["steam"]["players"], 7)
            self.assertIn("fetched_at", result["steam"])
            self.assertEqual(read.call_count, 2)
            self.assertIn("appid=123&loc=ja", read.call_args.args[0])
            self.assertIsNone(self.client.get("test-game")["steam"])

    def test_no_steam_store_causes_no_extra_request(self):
        with patch.object(
            self.client,
            "_read",
            return_value=GAME.replace('data-store="steam"', 'data-store="other"'),
        ) as read:
            self.assertIsNone(self.client.get("test-game", include_steam=True)["steam"])
            read.assert_called_once()

    def test_expiry_refreshes_without_stale_fallback(self):
        with patch.object(self.client, "_read", return_value=GAME):
            self.client.get("test-game")
        with (
            closing(
                sqlite3.connect(Path(self.directory.name) / "cache-v1.sqlite3")
            ) as db,
            db,
        ):
            db.execute("UPDATE responses SET fetched=0")
        with (
            patch.object(self.client, "_read", side_effect=FetchError("offline")),
            self.assertRaises(FetchError),
        ):
            self.client.get("test-game")

    def test_failed_parse_is_not_cached(self):
        with patch.object(
            self.client, "_read", side_effect=["<h1>Blocked</h1>", GAME]
        ) as read:
            with self.assertRaises(ParseError):
                self.client.get("test-game")
            self.assertEqual(self.client.get("test-game")["title"], "Test & Game")
            self.assertEqual(read.call_count, 2)

    def test_invalid_input_fails_before_network(self):
        with patch.object(
            self.client, "_read", side_effect=AssertionError("Unexpected network")
        ):
            calls = [
                lambda: self.client.get("../secret"),
                lambda: self.client.get("test", fields=["titel"]),
                lambda: self.client.get("test", fields=[]),
                lambda: self.client.search(" "),
                lambda: self.client.search("x", page=0),
                lambda: self.client.search("x", limit=-1),
            ]
            for call in calls:
                with self.assertRaises(ValueError):
                    call()

    def test_no_cache_and_zero_ttl(self):
        for client in (
            Client(cache_dir=None, min_interval=0),
            Client(cache_dir=self.directory.name, cache_ttl=0, min_interval=0),
        ):
            with patch.object(client, "_read", return_value=GAME) as read:
                client.get("test-game")
                client.get("test-game")
                self.assertEqual(read.call_count, 2)

    def test_throttle_shared_between_clients(self):
        first = Client(cache_dir=self.directory.name, min_interval=1)
        second = Client(cache_dir=self.directory.name, min_interval=1)
        with (
            patch("gamesneverdie.client.time.time", return_value=100),
            patch("gamesneverdie.client.time.sleep") as sleep,
        ):
            first._schedule_request()
            second._schedule_request()
            sleep.assert_called_once_with(1)

    def test_http_error_status_and_retry_after(self):
        self.client._opener.open = Mock()
        for status, error_type in (
            (404, NotFoundError),
            (403, FetchError),
            (429, FetchError),
        ):
            self.client._opener.open.side_effect = HTTPError(
                "url", status, "error", {"Retry-After": "60"}, None
            )
            with self.assertRaises(error_type) as caught:
                self.client.get("test-game")
            self.assertIn(
                str(status) if status != 404 else "not found", str(caught.exception)
            )

    def test_timeout_and_size_limit(self):
        self.client._opener.open = Mock(side_effect=TimeoutError("timeout"))
        with self.assertRaises(FetchError):
            self.client.get("test-game")
        response = Mock()
        response.__enter__ = Mock(return_value=response)
        response.__exit__ = Mock(return_value=False)
        response.read.return_value = b"x" * (MAX_RESPONSE_BYTES + 1)
        self.client._opener.open = Mock(return_value=response)
        with self.assertRaisesRegex(FetchError, "size limit"):
            self.client.get("test-game")

    def test_invalid_steam_response_fails(self):
        for body in ("<html>error</html>", "[]", '{"error":"unavailable"}'):
            client = Client(cache_dir=None, min_interval=0)
            with (
                patch.object(client, "_read", side_effect=[GAME, body]),
                self.assertRaises(ParseError),
            ):
                client.get("test-game", include_steam=True)

    def test_url_boundary_and_redirects(self):
        self.assertEqual(game_slug("https://gamesneverdie.com/ja/games/a--1/"), "a--1")
        for value in (
            "https://evil.com/ja/games/a",
            "https://gamesneverdie.com.evil/ja/games/a",
            "https://gamesneverdie.com/ja/games/a?q=1",
            "http://localhost/x",
            "a/b",
        ):
            with self.assertRaises(ValueError):
                game_slug(value)
        with self.assertRaises(FetchError):
            _SameOriginRedirects().redirect_request(
                None, None, 302, "", {}, "http://localhost/"
            )


class CliTests(unittest.TestCase):
    def test_piped_output_is_utf8_even_with_ascii_locale(self):
        result = subprocess.run(
            [sys.executable, "-m", "gamesneverdie", "fields"],
            env={**os.environ, "PYTHONIOENCODING": "ascii"},
            capture_output=True,
            check=True,
        )
        self.assertEqual(json.loads(result.stdout.decode("utf-8")), FIELDS)

    def test_json_stdout_and_stderr_errors(self):
        stdout, stderr = StringIO(), StringIO()
        with (
            redirect_stdout(stdout),
            redirect_stderr(stderr),
            patch.object(Client, "_read", return_value=GAME),
        ):
            status = main(
                ["get", "test-game", "--fields", "title,platforms", "--no-cache"]
            )
        self.assertEqual(status, 0)
        self.assertEqual(
            json.loads(stdout.getvalue()),
            {"title": "Test & Game", "platforms": ["PC", "Steam"]},
        )
        self.assertEqual(stderr.getvalue(), "")
        with redirect_stdout(StringIO()), redirect_stderr(stderr):
            self.assertEqual(main(["get", "../secret", "--no-cache"]), 1)
        self.assertIn("gnd:", stderr.getvalue())

    def test_output_never_overwrites_existing_file(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "game.json"
            with (
                patch.object(Client, "_read", return_value=GAME),
                redirect_stderr(StringIO()),
            ):
                args = [
                    "get",
                    "test-game",
                    "--no-cache",
                    "--fields",
                    "title",
                    "-o",
                    str(output),
                ]
                self.assertEqual(main(args), 0)
                original = output.read_text(encoding="utf-8")
                self.assertEqual(main(args), 1)
                self.assertEqual(output.read_text(encoding="utf-8"), original)


if __name__ == "__main__":
    unittest.main()
