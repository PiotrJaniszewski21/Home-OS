from __future__ import annotations

import html
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from email.utils import format_datetime
from html.parser import HTMLParser
from urllib.parse import quote, urlencode, urljoin, urlparse
from xml.etree import ElementTree

from core import NEWZNAB_NS, TORZNAB_NS, parse_size

DETAIL_PATH_RE = re.compile(
    r"^/post-detail/[a-f0-9]+/[A-Za-z0-9._~%+-]+/?$",
    re.IGNORECASE,
)
HEALTH_RE = re.compile(r"\[\s*([\d,]+)\s*/\s*([\d,]+)\s*\]")

CATEGORY_MAP = {
    "anime": 5070,
    "apps": 4000,
    "books": 7000,
    "docus": 5080,
    "documentaries": 5080,
    "e-books": 7000,
    "games": 1000,
    "movies": 2000,
    "music": 3000,
    "other": 8000,
    "tv": 5000,
    "xxx": 6000,
}

TORZNAB_CATEGORY_TO_SITE = {
    1000: "Games",
    2000: "Movies",
    3000: "Music",
    4000: "Apps",
    5000: "TV",
    5070: "Anime",
    5080: "Docus",
    6000: "XXX",
    7000: "Books",
    8000: "Other",
}


@dataclass(frozen=True)
class TorrentGalaxyResult:
    title: str
    detail_path: str
    category: int
    size: int
    seeders: int
    leechers: int
    uploader: str = ""
    published: datetime | None = None


def _classes(attributes: list[tuple[str, str | None]]) -> set[str]:
    value = next((value for key, value in attributes if key == "class"), "") or ""
    return set(value.split())


def _attribute(attributes: list[tuple[str, str | None]], name: str) -> str:
    return next((value or "" for key, value in attributes if key == name), "")


def _integer(value: str) -> int:
    digits = re.sub(r"\D", "", value)
    return int(digits) if digits else 0


def _detail_path(value: str) -> str:
    path = urlparse(html.unescape(value)).path
    return path if DETAIL_PATH_RE.fullmatch(path) else ""


def _category(value: str) -> int:
    path = urlparse(html.unescape(value)).path
    prefix = "/get-posts/category:"
    if not path.startswith(prefix):
        return 8000
    name = path[len(prefix) :].split(":", 1)[0].casefold()
    return CATEGORY_MAP.get(name, 8000)


def _published(value: str) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


class TorrentGalaxyTableParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.results: list[TorrentGalaxyResult] = []
        self._row: dict[str, object] | None = None
        self._row_depth = 0
        self._cell_depth: int | None = None
        self._cell_text: list[str] = []
        self._username = False
        self._username_text: list[str] = []

    def handle_starttag(
        self,
        tag: str,
        attributes: list[tuple[str, str | None]],
    ) -> None:
        classes = _classes(attributes)
        if tag == "div":
            if self._row is None:
                if "tgxtablerow" in classes:
                    self._row = {"cells": []}
                    self._row_depth = 1
                return
            if self._row_depth == 1 and "tgxtablecell" in classes:
                self._cell_depth = 2
                self._cell_text = []
            self._row_depth += 1
            return

        if self._row is None:
            return

        if tag == "a":
            href = html.unescape(_attribute(attributes, "href"))
            detail_path = _detail_path(href)
            title = html.unescape(_attribute(attributes, "title")).strip()
            if detail_path and title and "detail_path" not in self._row:
                self._row["detail_path"] = detail_path
                self._row["title"] = title
            if href.startswith("/get-posts/category:"):
                self._row["category"] = _category(href)
            if "username" in classes and "uploader" not in self._row:
                self._username = True
                self._username_text = []
        elif tag == "small" and "added-date" in classes:
            published = _published(_attribute(attributes, "data-timestamp"))
            if published is not None:
                self._row["published"] = published

    def handle_data(self, data: str) -> None:
        if self._row is None:
            return
        if self._cell_depth is not None:
            self._cell_text.append(data)
        if self._username:
            self._username_text.append(data)

    def handle_endtag(self, tag: str) -> None:
        if self._row is None:
            return
        if tag == "a" and self._username:
            uploader = " ".join(" ".join(self._username_text).split())
            if uploader:
                self._row["uploader"] = uploader
            self._username = False
            self._username_text = []
            return
        if tag != "div":
            return

        if self._cell_depth == self._row_depth:
            cells = self._row["cells"]
            if isinstance(cells, list):
                cells.append(" ".join(" ".join(self._cell_text).split()))
            self._cell_depth = None
            self._cell_text = []

        self._row_depth -= 1
        if self._row_depth == 0:
            self._finish_row()

    def _finish_row(self) -> None:
        if self._row is None:
            return
        cells = self._row.get("cells", [])
        cell_values = cells if isinstance(cells, list) else []
        size = next((parse_size(value) for value in cell_values if parse_size(value)), 0)
        seeders = 0
        leechers = 0
        for value in cell_values:
            match = HEALTH_RE.search(value)
            if match:
                seeders = _integer(match.group(1))
                leechers = _integer(match.group(2))
                break

        title = str(self._row.get("title", ""))
        detail_path = str(self._row.get("detail_path", ""))
        if title and DETAIL_PATH_RE.fullmatch(detail_path):
            self.results.append(
                TorrentGalaxyResult(
                    title=title,
                    detail_path=detail_path,
                    category=int(self._row.get("category", 8000)),
                    size=size,
                    seeders=seeders,
                    leechers=leechers,
                    uploader=str(self._row.get("uploader", "")),
                    published=self._row.get("published")
                    if isinstance(self._row.get("published"), datetime)
                    else None,
                )
            )
        self._row = None
        self._row_depth = 0
        self._cell_depth = None
        self._cell_text = []
        self._username = False
        self._username_text = []


def parse_torrentgalaxy_results(document: str) -> list[TorrentGalaxyResult]:
    parser = TorrentGalaxyTableParser()
    parser.feed(document)
    return parser.results


def torrentgalaxy_search_path(query: str, page: int = 0) -> str:
    path = f"/get-posts/keywords:{quote(query, safe='')}/"
    if page > 0:
        path += "?" + urlencode({"page": page})
    return path


def torrentgalaxy_category_path(category: int, page: int = 0) -> str:
    parent = category if category in TORZNAB_CATEGORY_TO_SITE else category // 1000 * 1000
    name = TORZNAB_CATEGORY_TO_SITE.get(parent, "Other")
    path = f"/get-posts/category:{quote(name, safe='')}:time:10D/"
    if page > 0:
        path += "?" + urlencode({"page": page})
    return path


def _add_text(parent: ElementTree.Element, tag: str, value: object) -> None:
    child = ElementTree.SubElement(parent, tag)
    child.text = str(value)


def _add_torznab_attribute(
    item: ElementTree.Element,
    name: str,
    value: object,
) -> None:
    ElementTree.SubElement(
        item,
        f"{{{TORZNAB_NS}}}attr",
        {"name": name, "value": str(value)},
    )


def build_torrentgalaxy_caps_xml() -> bytes:
    caps = ElementTree.Element("caps")
    ElementTree.SubElement(
        caps,
        "server",
        {"version": "1.0", "title": "Home OS TorrentGalaxy Browser Bridge"},
    )
    ElementTree.SubElement(caps, "limits", {"max": "50", "default": "50"})
    ElementTree.SubElement(
        caps,
        "registration",
        {"available": "no", "open": "no"},
    )
    searching = ElementTree.SubElement(caps, "searching")
    for name, parameters in (
        ("search", "q"),
        ("tv-search", "q,season,ep,imdbid"),
        ("movie-search", "q,imdbid"),
        ("music-search", "q"),
        ("book-search", "q"),
    ):
        ElementTree.SubElement(
            searching,
            name,
            {"available": "yes", "supportedParams": parameters},
        )
    categories = ElementTree.SubElement(caps, "categories")
    for category_id, name in (
        (1000, "Console"),
        (2000, "Movies"),
        (3000, "Audio"),
        (4000, "PC"),
        (5000, "TV"),
        (5070, "TV/Anime"),
        (5080, "TV/Documentary"),
        (6000, "XXX"),
        (7000, "Books"),
        (8000, "Other"),
    ):
        ElementTree.SubElement(
            categories,
            "category",
            {"id": str(category_id), "name": name},
        )
    return ElementTree.tostring(caps, encoding="utf-8", xml_declaration=True)


def build_torrentgalaxy_search_xml(
    results: list[TorrentGalaxyResult],
    *,
    base_url: str,
    api_key: str,
    site_url: str,
    offset: int = 0,
) -> bytes:
    rss = ElementTree.Element("rss", {"version": "2.0"})
    channel = ElementTree.SubElement(rss, "channel")
    _add_text(channel, "title", "Home OS TorrentGalaxy Browser Bridge")
    _add_text(
        channel,
        "description",
        "Browser-backed Torznab results from TorrentGalaxy",
    )
    _add_text(channel, "link", site_url)
    ElementTree.SubElement(
        channel,
        f"{{{NEWZNAB_NS}}}response",
        {"offset": str(offset), "total": str(offset + len(results))},
    )
    now = datetime.now(UTC)
    for result in results:
        download_query = urlencode({"path": result.detail_path, "apikey": api_key})
        download_url = (
            f"{base_url.rstrip('/')}/torrentgalaxy/download?{download_query}"
        )
        detail_url = urljoin(f"{site_url.rstrip('/')}/", result.detail_path)
        item = ElementTree.SubElement(channel, "item")
        _add_text(item, "title", result.title)
        guid = ElementTree.SubElement(item, "guid", {"isPermaLink": "false"})
        guid.text = detail_url
        _add_text(item, "link", download_url)
        _add_text(item, "comments", detail_url)
        _add_text(item, "pubDate", format_datetime(result.published or now))
        _add_text(item, "category", result.category)
        _add_text(item, "description", f"Uploader: {result.uploader}")
        ElementTree.SubElement(
            item,
            "enclosure",
            {
                "url": download_url,
                "length": str(result.size),
                "type": "application/x-bittorrent",
            },
        )
        for name, value in (
            ("category", result.category),
            ("size", result.size),
            ("seeders", result.seeders),
            ("peers", result.seeders + result.leechers),
            ("leechers", result.leechers),
            ("downloadvolumefactor", 0),
            ("uploadvolumefactor", 1),
        ):
            _add_torznab_attribute(item, name, value)
    return ElementTree.tostring(rss, encoding="utf-8", xml_declaration=True)
