from __future__ import annotations

import html
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from email.utils import format_datetime
from html.parser import HTMLParser
from urllib.parse import parse_qs, urlencode, urljoin, urlparse
from xml.etree import ElementTree

from core import NEWZNAB_NS, TORZNAB_NS, parse_size

DETAIL_PATH_RE = re.compile(r"^/details\.php\?id=\d+$")
INFO_HASH_RE = re.compile(r"^urn:btih:[A-Za-z0-9]+$", re.IGNORECASE)

SITE_CATEGORY_MAP = {
    0: 8000,
    1: 2000,
    2: 5000,
    3: 1000,
    4: 3000,
    5: 4000,
    6: 6000,
    7: 5070,
    8: 8000,
}


@dataclass(frozen=True)
class UindexResult:
    title: str
    detail_path: str
    magnet: str
    category: int
    size: int
    seeders: int
    leechers: int
    published: datetime | None = None


def _classes(attributes: list[tuple[str, str | None]]) -> set[str]:
    value = next((value for key, value in attributes if key == "class"), "") or ""
    return set(value.split())


def _attribute(attributes: list[tuple[str, str | None]], name: str) -> str:
    return next((value or "" for key, value in attributes if key == name), "")


def _integer(value: str) -> int:
    match = re.search(r"\d+", value.replace(",", ""))
    return int(match.group()) if match else 0


def _category_from_url(url: str) -> int:
    category = parse_qs(urlparse(url).query).get("c", ["0"])[0]
    return int(category) if category.isdigit() else 0


def _valid_magnet(value: str) -> bool:
    values = parse_qs(urlparse(value).query).get("xt", [])
    return any(INFO_HASH_RE.fullmatch(item) for item in values)


class UindexTableParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.results: list[UindexResult] = []
        self._row: dict[str, object] | None = None
        self._cell = ""
        self._capture_title = False
        self._text: list[str] = []

    def handle_starttag(
        self,
        tag: str,
        attributes: list[tuple[str, str | None]],
    ) -> None:
        if tag == "tr":
            self._row = {}
            return
        if self._row is None:
            return
        if tag == "td":
            classes = _classes(attributes)
            self._cell = next(
                (
                    name
                    for name in (
                        "sr-col-uploaded",
                        "sr-col-size",
                        "sr-col-seeders",
                        "sr-col-leechers",
                    )
                    if name in classes
                ),
                "",
            )
            self._text = []
            return
        if tag != "a":
            return
        href = html.unescape(_attribute(attributes, "href"))
        if DETAIL_PATH_RE.fullmatch(href) and "detail_path" not in self._row:
            self._row["detail_path"] = href
            self._capture_title = True
            self._text = []
        elif href.startswith("magnet:?") and _valid_magnet(href):
            self._row["magnet"] = href
        elif "search.php?" in href and "c=" in href:
            self._row["site_category"] = _category_from_url(href)

    def handle_data(self, data: str) -> None:
        if self._row is not None and (self._cell or self._capture_title):
            self._text.append(data)

    def handle_endtag(self, tag: str) -> None:
        if self._row is None:
            return
        text = " ".join(" ".join(self._text).split())
        if tag == "a" and self._capture_title:
            self._row["title"] = text
            self._capture_title = False
            self._text = []
            return
        if tag == "td":
            if self._cell == "sr-col-size":
                self._row["size"] = parse_size(text)
            elif self._cell == "sr-col-seeders":
                self._row["seeders"] = _integer(text)
            elif self._cell == "sr-col-leechers":
                self._row["leechers"] = _integer(text)
            self._cell = ""
            self._text = []
            return
        if tag != "tr":
            return
        detail_path = str(self._row.get("detail_path", ""))
        magnet = str(self._row.get("magnet", ""))
        title = str(self._row.get("title", ""))
        if DETAIL_PATH_RE.fullmatch(detail_path) and magnet and title:
            site_category = int(self._row.get("site_category", 0))
            self.results.append(
                UindexResult(
                    title=title,
                    detail_path=detail_path,
                    magnet=magnet,
                    category=SITE_CATEGORY_MAP.get(site_category, 8000),
                    size=int(self._row.get("size", 0)),
                    seeders=int(self._row.get("seeders", 0)),
                    leechers=int(self._row.get("leechers", 0)),
                )
            )
        self._row = None
        self._cell = ""
        self._text = []


def parse_uindex_results(document: str) -> list[UindexResult]:
    parser = UindexTableParser()
    parser.feed(document)
    return parser.results


def uindex_search_path(query: str) -> str:
    return "/search.php?" + urlencode({"search": query, "c": 0})


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


def build_uindex_caps_xml() -> bytes:
    caps = ElementTree.Element("caps")
    ElementTree.SubElement(
        caps,
        "server",
        {"version": "1.0", "title": "Home OS Uindex Browser Bridge"},
    )
    ElementTree.SubElement(caps, "limits", {"max": "100", "default": "50"})
    ElementTree.SubElement(
        caps,
        "registration",
        {"available": "no", "open": "no"},
    )
    searching = ElementTree.SubElement(caps, "searching")
    for name, parameters in (
        ("search", "q"),
        ("tv-search", "q,season,ep"),
        ("movie-search", "q"),
        ("music-search", "q"),
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
        (6000, "XXX"),
        (8000, "Other"),
    ):
        ElementTree.SubElement(
            categories,
            "category",
            {"id": str(category_id), "name": name},
        )
    return ElementTree.tostring(caps, encoding="utf-8", xml_declaration=True)


def build_uindex_search_xml(
    results: list[UindexResult],
    *,
    site_url: str,
    offset: int = 0,
) -> bytes:
    rss = ElementTree.Element("rss", {"version": "2.0"})
    channel = ElementTree.SubElement(rss, "channel")
    _add_text(channel, "title", "Home OS Uindex Browser Bridge")
    _add_text(channel, "description", "Browser-backed Torznab results from Uindex")
    _add_text(channel, "link", site_url)
    ElementTree.SubElement(
        channel,
        f"{{{NEWZNAB_NS}}}response",
        {"offset": str(offset), "total": str(offset + len(results))},
    )
    now = datetime.now(UTC)
    for result in results:
        detail_url = urljoin(f"{site_url.rstrip('/')}/", result.detail_path)
        item = ElementTree.SubElement(channel, "item")
        _add_text(item, "title", result.title)
        guid = ElementTree.SubElement(item, "guid", {"isPermaLink": "false"})
        guid.text = detail_url
        _add_text(item, "link", result.magnet)
        _add_text(item, "comments", detail_url)
        _add_text(item, "pubDate", format_datetime(result.published or now))
        _add_text(item, "category", result.category)
        ElementTree.SubElement(
            item,
            "enclosure",
            {
                "url": result.magnet,
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
            ("magneturl", result.magnet),
            ("downloadvolumefactor", 0),
            ("uploadvolumefactor", 1),
        ):
            _add_torznab_attribute(item, name, value)
    return ElementTree.tostring(rss, encoding="utf-8", xml_declaration=True)
