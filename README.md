# gamesneverdie-client

A lightweight, unofficial client for [GAMES NEVER DIE!](https://gamesneverdie.com/ja).
Search for games and retrieve selected metadata as JSON from a CLI or Python.
Python 3.11+; **zero runtime dependencies**, no browser, API key, account or running server.

## Install

Install the CLI directly from GitHub with [uv](https://docs.astral.sh/uv/):

```sh
uv tool install git+https://github.com/tetsuya-dev-jp/gamesneverdie-client.git
gnd --version
```

Or install the library and CLI into a Python environment:

```sh
python -m pip install git+https://github.com/tetsuya-dev-jp/gamesneverdie-client.git
```

No package has been published to PyPI. A one-off command also works:

```sh
uvx --from git+https://github.com/tetsuya-dev-jp/gamesneverdie-client.git gnd get elden-ring --fields title,release_date,platforms
```

## CLI

Search in Japanese or English. Each call retrieves **one** search page and never
fetches the individual result pages:

```sh
gnd search 'ゼルダ' --limit 5
gnd search 'zelda' --page 2
```

Search results contain `query`, `page`, `total`, `total_pages`, `fetched_at` and
`items`. Each item has `slug`, `url`, `title`, `cover_url`, `rating` (the displayed
card score) and `subtitle` (the site's year/genre label). `--limit` trims that
page locally; it does not change `total` or follow pagination. A search with no
matches returns an empty list and `total_pages: 0`.

Get a game by slug or full URL:

```sh
gnd get elden-ring --fields title,release_date,developers,platforms
gnd get https://gamesneverdie.com/ja/games/outer-wilds
gnd fields
```

`--fields` selects comma-separated **top-level** keys. Unknown keys fail before a
request. Omit it for all standard game metadata. Output is UTF-8 JSON; messages
go to stderr. Success exits with 0, lookup/network/parse/file errors with 1,
and argument syntax errors with 2.

Optional Steam data needs an additional request:

```sh
gnd get elden-ring --fields title,steam
gnd get elden-ring --steam
gnd get elden-ring --fields steam | jq '.steam.price'
```

`steam` contains the site's original JSON keys (`players`, `playersBase`,
`review`, `reviewLocal`, `price`, `history`) plus its own `fetched_at`. Selecting
`steam` or passing `--steam` fetches it; ordinary lookups leave it `null`. Games
without a Steam store link also return `null`. Unavailable or malformed endpoint
responses raise an error rather than reporting an invented price or player count.

Save JSON or pipe it to another program:

```sh
gnd get outer-wilds --fields title,release_dates,platforms --output game.json
gnd get elden-ring --fields title,stores | jq '.stores[] | select(.store == "steam")'
```

`--output` creates a new file and refuses to overwrite an existing one. Shell
redirection (`> file.json`) follows your shell's usual overwrite behavior.

## Python API

```python
from gamesneverdie import Client, FetchError, NotFoundError, ParseError

client = Client()
page = client.search("Outer Wilds", limit=5)
for item in page["items"]:
    print(item["slug"], item["title"])

game = client.get(
    "outer-wilds",
    fields=["title", "url", "developers", "release_dates", "platforms"],
)
print(game)

# Optional extra request, with a shorter cache lifetime.
price = client.get("elden-ring", fields=["steam"])["steam"]["price"]
```

`Client.get(identifier, *, fields=None, include_steam=False)` returns a dictionary.
`Client.search(query, *, page=1, limit=None)` returns the search envelope described
above. `identifier` accepts a slug or an HTTPS Japanese game-page URL; fragments
and a trailing slash are accepted, query parameters and other hosts are rejected.

`NotFoundError` (a `FetchError` subclass) represents HTTP 404. `FetchError` covers
transport and other HTTP errors, including 403/429; `ParseError` reports changed
or unexpected page/endpoint shapes. Invalid values raise `ValueError`; an
identifier of the wrong type raises `TypeError`. Disk
cache errors are surfaced as `OSError` or `sqlite3.Error`.

The CLI is also an integration interface for Node.js, shell scripts and other
languages; see [examples/node.mjs](examples/node.mjs). It prints one JSON document
and needs no HTTP server. This project does not provide a hosted REST service.

## Game fields

Run `gnd fields` for the machine-readable field list.

| Keys | Meaning |
| --- | --- |
| `slug`, `url`, `title` | Identifier, source page and displayed title |
| `description` | Overview, with paragraph breaks preserved |
| `developers`, `publishers`, `directors` | Lists of displayed names |
| `release_date`, `release_date_scope` | First displayed date and its region |
| `release_dates` | All displayed `{date, scope}` entries |
| `platforms`, `tags` | Lists of platform names and genres/themes |
| `age_rating`, `status` | Displayed age rating and distribution/development status |
| `ratings` | `{igdb, steam}`; IGDB score and Steam positive-review percentage, on 0–100 scales |
| `cover_url`, `hero_url` | Image URLs; no image download |
| `screenshots`, `trailers` | Screenshot and YouTube video URLs |
| `stores` | List of `{store, name, url}` |
| `articles` | Game-specific `{title, url, source, date, category, summary}` entries |
| `related` | Related `{slug, title, url}` entries |
| `updated_at` | Date displayed beside the page's update request |
| `fetched_at` | UTC timestamp when this source response was fetched |
| `steam` | Opt-in Steam JSON with its own `fetched_at`, otherwise `null` |

Absent scalar facts are `null`; absent collections are `[]`. Names, dates,
summaries and labels follow the Japanese site. Dates may be partial or
unannounced: no missing day/month is fabricated. When Japan and worldwide dates
are both displayed, `release_dates` keeps both (`scope` is typically `JP` or
`world`); the first displayed date is not necessarily the earliest release.
Site metadata is reported, not independently corrected or verified.

## Cache, request limits and freshness

Defaults:

- Game metadata: cached for 24 hours.
- Search pages: cached for 1 hour.
- Optional Steam data: cached for 5 minutes.
- Requests: at least 1 second between scheduled request starts, shared across
  clients/processes using the same cache directory.
- Network timeout: 20 seconds per blocking network operation; responses are
  limited to 8 MiB. There are no automatic retries or stale-on-error responses.

Cache hits require no network requests and retain the original `fetched_at`.
Selecting fewer fields reduces returned JSON, not the HTML that the site sends.
The normal detail lookup fetches one HTML document; it does not fetch scripts,
images, article bodies or linked games. Network time depends on the site and
connection; this project makes no universal latency guarantee.

SQLite data is stored at `$XDG_CACHE_HOME/gamesneverdie/cache-v1.sqlite3`, or
`~/.cache/gamesneverdie/cache-v1.sqlite3` by default. On Windows the default is
under `%LOCALAPPDATA%`. Deleting that directory clears cached data and scheduling
state when no clients are running. There is no background process.

```sh
gnd get elden-ring --refresh           # Ignore cached responses this time
gnd get elden-ring --no-cache          # Neither read nor write the disk cache
gnd get elden-ring --cache-dir ./cache --timeout 10
```

Library options: `Client(cache_dir=..., cache_ttl=86400, timeout=20, min_interval=1)`.
Search/Steam lifetimes are capped at 3600/300 seconds even with a longer
`cache_ttl`. Set `cache_ttl=0` to refresh; `cache_dir=None` disables disk caching.
Without a shared cache, request spacing is local to that client instance.

## Upstream limits

This is an unofficial client for individual, user-initiated lookups. It is not
affiliated with GAMES NEVER DIE! and uses unversioned public HTML plus, when
requested, the site's internal Steam endpoint. Layout or endpoint changes can
break extraction; errors are explicit rather than silently returning empty games.

The site's [disclaimer, article 6](https://gamesneverdie.com/ja/disclaimer) asks
users to refrain from automated bulk extraction, and its
[robots.txt](https://gamesneverdie.com/robots.txt) excludes search and API paths
from crawling. This client provides no bulk crawler, sitemap downloader or
automatic pagination. Use it within the site's requirements; caching and
spacing requests do not grant permission for bulk collection. Stop on 403/429;
the client reports `Retry-After` when provided and does not retry.

The MIT license covers this client code only. Game descriptions, articles,
images and other upstream material remain subject to their respective rights.
Keep source URLs with data you use.

## Development

```sh
uv sync
uv run python -m unittest discover -s tests -v
uv build
```

Offline tests use small synthetic HTML fixtures, not a redistributed game
database or captured sessions. CI exercises supported Python versions and
Linux, macOS and Windows. Live lookups are deliberately excluded from automatic
tests to avoid repeatedly requesting the upstream site.

MIT licensed. See [LICENSE](LICENSE).
