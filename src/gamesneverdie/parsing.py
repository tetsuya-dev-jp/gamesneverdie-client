"""Normalize the public Japanese HTML, keeping absent facts as null or empty lists."""

import re
from urllib.parse import urljoin, urlsplit

from .html import Node, parse_html

BASE_URL = "https://gamesneverdie.com"
FIELDS = {
    "slug": "Game identifier used in the page URL",
    "url": "Source page URL",
    "title": "Displayed title",
    "description": "Overview, preserving paragraph breaks",
    "developers": "Developer names",
    "publishers": "Publisher names",
    "directors": "Director names",
    "release_date": "Displayed release date (may be year/month only or unannounced)",
    "release_date_scope": "Displayed date scope, e.g. world or jp",
    "release_dates": "All displayed dates with their scope; release_date is the first displayed date",
    "platforms": "Displayed platform names",
    "tags": "Displayed genres and themes",
    "age_rating": "Displayed age rating",
    "status": "Displayed distribution/development status",
    "ratings": "Displayed IGDB score and Steam positive-review percentage (0–100)",
    "cover_url": "Cover image URL; image is not downloaded",
    "hero_url": "Hero image URL",
    "screenshots": "Screenshot URLs",
    "trailers": "YouTube video URLs",
    "stores": "Store name, identifier and URL",
    "articles": "Game-specific article title, URL, source, date, category and summary",
    "related": "Related games (slug, title, URL)",
    "updated_at": "Page's displayed last-update date, if present",
    "fetched_at": "UTC timestamp of the source fetch, retained on cache hits",
    "steam": "Optional site-provided Steam JSON; fetched only when requested",
}


class ParseError(Exception):
    """The response does not have the expected game or search structure."""


def image(node: Node) -> str | None:
    return node.first("img").attrs.get("src")


def number(text: str) -> int | float | None:
    if not re.fullmatch(r"\d+(?:\.\d+)?%?", text):
        return None
    value = float(text.rstrip("%"))
    return int(value) if value.is_integer() else value


def game_link(node: Node) -> dict | None:
    url = urlsplit(node.attrs.get("href") or "")
    if url.netloc and url.netloc != "gamesneverdie.com":
        return None
    path = url.path
    match = re.fullmatch(r"/ja/games/([a-z0-9][a-z0-9-]*)", path)
    if not match:
        return None
    return {
        "slug": match[1],
        "title": node.first(cls="title").text()
        or node.attrs.get("aria-label")
        or node.text(),
        "url": BASE_URL + path,
    }


def parse_game(html: str, slug: str) -> dict:
    root = parse_html(html)
    hero = root.first(id="hero")
    title = hero.first("h1").text()
    details = root.first(id="details")
    if not title or not details.tag:
        raise ParseError(
            "Expected game title and details section; the site layout may have changed"
        )
    rows = {r.first("dt").text(): r.first("dd") for r in details.find(cls="r")}

    def row(label: str) -> Node:
        return rows.get(label, Node())

    def names(label: str) -> list[str]:
        return [s.strip() for s in row(label).text().split("、") if s.strip()]

    stores = [
        {
            "store": a.attrs["data-store"],
            "name": a.text(),
            "url": urljoin(BASE_URL, a.attrs.get("href") or ""),
        }
        for a in hero.find("a")
        if a.attrs.get("data-store")
    ]
    ratings = {
        a.first(cls="l").text(): number(a.first(cls="n").text())
        for a in hero.find("a", cls="c")
    }
    media = root.first(id="media")
    screenshots = list(
        dict.fromkeys(
            i.attrs["src"]
            for i in media.find("img")
            if "/t_screenshot_" in (i.attrs.get("src") or "")
        )
    )
    videos = [
        match[1]
        for i in media.find("img")
        if (
            match := re.match(
                r"https://i\.ytimg\.com/vi/([\w-]+)/", i.attrs.get("src") or ""
            )
        )
    ]
    articles = []
    for a in root.first(id="articles").find("a", cls="card-link"):
        articles.append(
            {
                "title": a.first("h3").text(),
                "url": a.attrs.get("href"),
                "source": a.first("cite").text() or None,
                "date": a.first("time").text() or None,
                "category": a.first(cls="art-tag").text() or None,
                "summary": a.first(cls="text-box").text() or None,
            }
        )
    related = {}
    for a in root.first(id="related").find("a"):
        link = game_link(a)
        if link:
            related[link["slug"]] = link
    updated = re.search(r"\d{4}-\d{2}-\d{2}", root.first(cls="ur-updated").text())
    dates = [
        {
            "date": span.first("time").text() or span.text(),
            "scope": span.first("svg").attrs.get("aria-label"),
        }
        for span in row("発売日").find(cls="date-scope")
    ]
    primary_date = dates[0] if dates else {"date": row("発売日").text(), "scope": None}
    about = root.first(cls="about-body")
    paragraphs = [p.text() for p in about.find("p")]
    return {
        "slug": slug,
        "url": f"{BASE_URL}/ja/games/{slug}",
        "title": title,
        "description": "\n\n".join(paragraphs) or about.text() or None,
        "developers": names("開発元"),
        "publishers": names("販売元"),
        "directors": names("ディレクター"),
        "release_date": primary_date["date"] or None,
        "release_date_scope": primary_date["scope"],
        "release_dates": dates,
        "platforms": [a.text() for a in row("プラットフォーム").find("a")],
        "tags": [a.text() for a in row("タグ").find("a")],
        "age_rating": row("年齢区分").text() or None,
        "status": row("販売状況").text() or None,
        "ratings": {"igdb": ratings.get("igdb"), "steam": ratings.get("steam")},
        "cover_url": image(hero.first(cls="cover")),
        "hero_url": image(hero.first(cls="keyart")),
        "screenshots": screenshots,
        "trailers": [
            f"https://www.youtube.com/watch?v={v}" for v in dict.fromkeys(videos)
        ],
        "stores": stores,
        "articles": articles,
        "related": list(related.values()),
        "updated_at": updated[0] if updated else None,
        "steam": None,
    }


def parse_search(html: str, query: str, page: int) -> dict:
    root = parse_html(html).first("main")
    hits = root.first(cls="hits").text()
    count = re.search(r"([\d,]+)\s*件", hits)
    if not count and any(
        p.text() == "該当するタイトルがありません" for p in root.find("p")
    ):
        return {"query": query, "page": page, "total": 0, "total_pages": 0, "items": []}
    if not count:
        raise ParseError(
            "Expected search result count; the site layout may have changed"
        )
    items = []
    seen = set()
    for a in root.first("ul", cls="card").find("a"):
        item = game_link(a)
        if item and item["slug"] not in seen:
            seen.add(item["slug"])
            item.update(
                cover_url=image(a),
                rating=number(a.first(cls="score").text()),
                subtitle=a.first(cls="meta").text() or None,
            )
            items.append(item)
    total = int(count[1].replace(",", ""))
    if total and not items:
        raise ParseError("Search reports matches but no game cards could be parsed")
    position = re.search(r"PAGE\s+(\d+)\s*/\s*(\d+)", root.first(cls="pos").text())
    return {
        "query": query,
        "page": page,
        "total": total,
        "total_pages": int(position[2]) if position else 1,
        "items": items,
    }


def validate_fields(fields: list[str] | tuple[str, ...] | None) -> None:
    if fields is not None:
        unknown = set(fields) - FIELDS.keys()
        if unknown or not fields:
            raise ValueError(
                f"Unknown or empty fields: {', '.join(sorted(unknown))}. Use gnd fields"
            )


def select_fields(record: dict, fields: list[str] | tuple[str, ...] | None) -> dict:
    validate_fields(fields)
    return {name: record[name] for name in fields} if fields is not None else record
