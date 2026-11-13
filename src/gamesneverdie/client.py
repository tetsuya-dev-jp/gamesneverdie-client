"""HTTP transport, bounded on-demand requests and a persistent SQLite cache."""

import json
import math
import os
import re
import sqlite3
import time
from collections.abc import Callable
from datetime import UTC, datetime
from http.client import HTTPException
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

from .parsing import (
    BASE_URL,
    ParseError,
    parse_game,
    parse_search,
    select_fields,
    validate_fields,
)

USER_AGENT = (
    "gamesneverdie-client/0.1 (+https://github.com/tetsuya-dev-jp/gamesneverdie-client)"
)
MAX_RESPONSE_BYTES = 8 * 1024 * 1024
DEFAULT_CACHE_DIR = (
    Path(
        os.environ.get("LOCALAPPDATA" if os.name == "nt" else "XDG_CACHE_HOME")
        or Path.home() / ".cache"
    )
    / "gamesneverdie"
)


class FetchError(Exception):
    """A network, HTTP or invalid-response error. Requests are never silently retried."""


class NotFoundError(FetchError):
    """The requested game was not found (HTTP 404)."""


class _SameOriginRedirects(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        target = urlsplit(newurl)
        if target.scheme != "https" or target.netloc != "gamesneverdie.com":
            raise FetchError("Refusing a redirect outside https://gamesneverdie.com")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def game_slug(identifier: str) -> str:
    if not isinstance(identifier, str):
        raise TypeError(
            "Expected a game slug or https://gamesneverdie.com/ja/games/<slug> URL"
        )
    value = identifier.strip()
    if value.startswith("https://"):
        url = urlsplit(value)
        if url.netloc != "gamesneverdie.com" or url.query:
            raise ValueError(
                "Expected a gamesneverdie.com game URL without query parameters"
            )
        match = re.fullmatch(r"/ja/games/([a-z0-9][a-z0-9-]*)/?", url.path)
        value = match[1] if match else ""
    if not re.fullmatch(r"[a-z0-9][a-z0-9-]*", value):
        raise ValueError(
            "Expected a game slug or https://gamesneverdie.com/ja/games/<slug> URL"
        )
    return value


def _timestamp(epoch: float) -> str:
    return datetime.fromtimestamp(epoch, UTC).isoformat(timespec="seconds")


class Client:
    """Fetch Japanese game pages without a browser, API key or runtime dependencies.

    cache_dir=None disables disk caching. The default cache is shared by CLI and
    library users, including a request interval shared across those processes.
    """

    def __init__(
        self,
        *,
        cache_dir: str | Path | None = DEFAULT_CACHE_DIR,
        cache_ttl: float = 86400,
        timeout: float = 20,
        min_interval: float = 1.0,
    ) -> None:
        for name, value in (
            ("cache_ttl", cache_ttl),
            ("timeout", timeout),
            ("min_interval", min_interval),
        ):
            if (
                not math.isfinite(value)
                or value < 0
                or (name == "timeout" and value == 0)
            ):
                raise ValueError(
                    f"{name} must be finite and {'positive' if name == 'timeout' else 'nonnegative'}"
                )
        self.cache_dir = Path(cache_dir) if cache_dir is not None else None
        self.cache_ttl = cache_ttl
        self.timeout = timeout
        self.min_interval = min_interval
        self._next_request = 0.0
        self._opener = build_opener(_SameOriginRedirects())

    def _cache(self) -> sqlite3.Connection:
        assert self.cache_dir is not None
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(self.cache_dir / "cache-v1.sqlite3", timeout=30)
        connection.execute(
            "CREATE TABLE IF NOT EXISTS responses (url TEXT PRIMARY KEY, fetched REAL, data TEXT)"
        )
        connection.execute(
            "CREATE TABLE IF NOT EXISTS throttle (id INTEGER PRIMARY KEY, next_request REAL)"
        )
        return connection

    def _schedule_request(self) -> None:
        now = time.time()
        if self.cache_dir is None:
            due = max(now, self._next_request)
            self._next_request = due + self.min_interval
        else:
            connection = self._cache()
            try:
                with connection:
                    connection.execute("BEGIN IMMEDIATE")
                    row = connection.execute(
                        "SELECT next_request FROM throttle WHERE id=1"
                    ).fetchone()
                    due = max(now, row[0] if row else now)
                    connection.execute(
                        "INSERT OR REPLACE INTO throttle VALUES (1, ?)",
                        (due + self.min_interval,),
                    )
            finally:
                connection.close()
        if due > now:
            time.sleep(due - now)

    def _read(self, url: str) -> str:
        self._schedule_request()
        request = Request(
            url,
            headers={
                "User-Agent": USER_AGENT,
                "Accept": "text/html,application/json",
                "Accept-Language": "ja",
            },
        )
        try:
            with self._opener.open(request, timeout=self.timeout) as response:
                body = response.read(MAX_RESPONSE_BYTES + 1)
                if len(body) > MAX_RESPONSE_BYTES:
                    raise FetchError("Response exceeds the 8 MiB size limit")
                return body.decode("utf-8")
        except HTTPError as exc:
            try:
                if exc.code == 404:
                    raise NotFoundError(f"Game not found: {url}") from exc
                retry_after = exc.headers.get("Retry-After")
                hint = f"; Retry-After: {retry_after}" if retry_after else ""
                raise FetchError(
                    f"HTTP {exc.code} for {url}{hint}; no retry performed"
                ) from exc
            finally:
                exc.close()
        except (URLError, TimeoutError, OSError, UnicodeError, HTTPException) as exc:
            raise FetchError(f"Unable to fetch {url}: {exc}") from exc

    def _fetch(self, url: str, parse: Callable[[str], dict], ttl: float) -> dict:
        connection = self._cache() if self.cache_dir is not None else None
        try:
            if connection is not None:
                row = connection.execute(
                    "SELECT fetched, data FROM responses WHERE url=?", (url,)
                ).fetchone()
                if row and 0 <= time.time() - row[0] < ttl:
                    return json.loads(row[1])
            result = parse(self._read(url))
            fetched = time.time()
            result["fetched_at"] = _timestamp(fetched)
            if connection is not None:
                with connection:
                    connection.execute(
                        "INSERT OR REPLACE INTO responses VALUES (?, ?, ?)",
                        (url, fetched, json.dumps(result, ensure_ascii=False)),
                    )
            return result
        finally:
            if connection is not None:
                connection.close()

    def search(self, query: str, *, page: int = 1, limit: int | None = None) -> dict:
        """Fetch one search page. limit truncates locally; it never triggers more requests."""
        if not isinstance(query, str) or not query.strip() or len(query) > 500:
            raise ValueError("query must contain 1–500 characters")
        if type(page) is not int or page < 1:
            raise ValueError("page must be a positive integer")
        if limit is not None and (type(limit) is not int or limit < 1):
            raise ValueError("limit must be a positive integer")
        query = query.strip()
        params = {"q": query}
        if page != 1:
            params["page"] = page
        result = self._fetch(
            f"{BASE_URL}/ja/search?{urlencode(params)}",
            lambda html: parse_search(html, query, page),
            min(self.cache_ttl, 3600),
        )
        if limit is not None:
            result["items"] = result["items"][:limit]
        return result

    def get(
        self,
        identifier: str,
        *,
        fields: list[str] | tuple[str, ...] | None = None,
        include_steam: bool = False,
    ) -> dict:
        """Fetch a slug or game URL. Selecting 'steam' requests the extra Steam endpoint."""
        validate_fields(fields)
        slug = game_slug(identifier)
        result = self._fetch(
            f"{BASE_URL}/ja/games/{slug}",
            lambda html: parse_game(html, slug),
            self.cache_ttl,
        )
        if include_steam or (fields is not None and "steam" in fields):
            store = next((s for s in result["stores"] if s["store"] == "steam"), None)
            app = re.search(r"/app/(\d+)", store["url"]) if store else None
            if app:

                def parse_steam(body: str) -> dict:
                    try:
                        data = json.loads(body)
                    except json.JSONDecodeError as exc:
                        raise ParseError(
                            "Steam endpoint returned invalid JSON"
                        ) from exc
                    if (
                        not isinstance(data, dict)
                        or not {"players", "review", "price"} <= data.keys()
                    ):
                        raise ParseError(
                            "Steam endpoint returned an unexpected response shape"
                        )
                    return data

                result["steam"] = self._fetch(
                    f"{BASE_URL}/api/steam?{urlencode({'appid': app[1], 'loc': 'ja'})}",
                    parse_steam,
                    min(self.cache_ttl, 300),
                )
        return select_fields(result, fields)
