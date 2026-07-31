from __future__ import annotations

import base64
import binascii
import html
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from email.utils import format_datetime
from html.parser import HTMLParser
from urllib.parse import parse_qs, quote, urlencode, urlparse
from xml.etree import ElementTree

TORZNAB_NS = "http://torznab.com/schemas/2015/feed"
NEWZNAB_NS = "http://www.newznab.com/DTD/2010/feeds/attributes/"
ElementTree.register_namespace("torznab", TORZNAB_NS)
ElementTree.register_namespace("newznab", NEWZNAB_NS)

DETAIL_PATH_RE = re.compile(r"^/torrent/\d+/[A-Za-z0-9._~%+-]+/?$")
SIZE_RE = re.compile(r"(\d+(?:\.\d+)?)\s*([KMGTPE]?B)", re.IGNORECASE)

SITE_CATEGORY_MAP = {
    1: 2070,
    2: 2030,
    3: 2000,
    4: 2010,
    5: 5000,
    6: 5000,
    7: 5000,
    9: 5080,
    10: 4050,
    11: 1080,
    12: 1020,
    13: 1040,
    14: 1050,
    15: 1080,
    16: 1090,
    17: 4040,
    18: 4000,
    19: 4030,
    20: 4000,
    21: 4000,
    22: 3010,
    23: 3040,
    24: 3000,
    25: 3020,
    26: 3000,
    27: 3050,
    28: 5070,
    33: 8000,
    34: 7000,
    35: 8000,
    36: 7020,
    37: 8000,
    38: 8000,
    39: 7030,
    40: 8010,
    41: 5040,
    42: 2040,
    43: 1080,
    44: 1030,
    45: 1010,
    46: 1090,
    47: 8000,
    48: 6010,
    49: 6060,
    50: 6000,
    51: 6000,
    52: 3030,
    53: 3000,
    54: 2040,
    55: 2000,
    56: 4070,
    57: 4060,
    58: 3000,
    59: 3000,
    60: 3000,
    66: 2060,
    67: 6000,
    68: 3000,
    69: 3000,
    70: 2040,
    71: 5000,
    72: 1110,
    73: 2000,
    74: 5000,
    75: 5030,
    76: 2045,
    77: 1180,
    78: 5070,
    79: 5070,
    80: 5070,
    81: 5070,
    82: 1090,
}


@dataclass(frozen=True)
class SearchResult:
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
    match = re.search(r"\d+", value.replace(",", ""))
    return int(match.group()) if match else 0


def parse_size(value: str) -> int:
    match = SIZE_RE.search(value)
    if not match:
        return 0
    number = float(match.group(1))
    unit = match.group(2).upper()
    exponent = {"B": 0, "KB": 1, "MB": 2, "GB": 3, "TB": 4, "PB": 5}[unit]
    return int(number * (1024**exponent))


def _title_from_path(path: str) -> str:
    parts = path.strip("/").split("/", 2)
    if len(parts) != 3:
        return ""
    return html.unescape(parts[2]).replace("-", " ")


class ResultTableParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.results: list[SearchResult] = []
        self._row: dict[str, object] | None = None
        self._cell = ""
        self._title_link = False
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
                    for name in ("coll-1", "coll-2", "coll-3", "coll-4", "coll-5")
                    if name in classes
                ),
                "",
            )
            self._text = []
            return
        if tag != "a":
            return
        href = _attribute(attributes, "href")
        if href.startswith("/torrent/") and "detail_path" not in self._row:
            self._row["detail_path"] = href
            self._title_link = True
            self._text = []
        elif href.startswith("/sub/") and "site_category" not in self._row:
            parts = href.strip("/").split("/")
            if len(parts) > 1 and parts[1].isdigit():
                self._row["site_category"] = int(parts[1])

    def handle_data(self, data: str) -> None:
        if self._row is not None and (self._cell or self._title_link):
            self._text.append(data)

    def handle_endtag(self, tag: str) -> None:
        if self._row is None:
            return
        text = " ".join(" ".join(self._text).split())
        if tag == "a" and self._title_link:
            self._row["title"] = text
            self._title_link = False
            self._text = []
            return
        if tag == "td":
            if self._cell == "coll-2":
                self._row["seeders"] = _integer(text)
            elif self._cell == "coll-3":
                self._row["leechers"] = _integer(text)
            elif self._cell == "coll-4":
                self._row["size"] = parse_size(text)
            elif self._cell == "coll-5":
                self._row["uploader"] = text
            self._cell = ""
            self._text = []
            return
        if tag != "tr":
            return
        detail_path = str(self._row.get("detail_path", ""))
        title = str(self._row.get("title", ""))
        if DETAIL_PATH_RE.fullmatch(detail_path):
            if not title or "..." in title:
                title = _title_from_path(detail_path)
            site_category = int(self._row.get("site_category", 0))
            self.results.append(
                SearchResult(
                    title=title,
                    detail_path=detail_path,
                    category=SITE_CATEGORY_MAP.get(site_category, 8000),
                    size=int(self._row.get("size", 0)),
                    seeders=int(self._row.get("seeders", 0)),
                    leechers=int(self._row.get("leechers", 0)),
                    uploader=str(self._row.get("uploader", "")),
                )
            )
        self._row = None
        self._cell = ""
        self._text = []


def parse_search_results(document: str) -> list[SearchResult]:
    parser = ResultTableParser()
    parser.feed(document)
    return parser.results


def build_search_query(parameters: dict[str, str]) -> str:
    parts = [parameters.get("q", "").strip()]
    if not parts[0]:
        parts[0] = parameters.get("imdbid", "").strip()
    season = parameters.get("season", "").strip()
    episode = parameters.get("ep", "").strip()
    if season and episode:
        parts.append(f"S{int(season):02d}E{int(episode):02d}")
    elif season:
        parts.append(f"S{int(season):02d}")
    return " ".join(part for part in parts if part)


def search_path(query: str, page: int) -> str:
    return f"/search/{quote(query, safe='')}/{max(1, page)}/"


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


def build_caps_xml() -> bytes:
    caps = ElementTree.Element("caps")
    ElementTree.SubElement(
        caps,
        "server",
        {"version": "1.0", "title": "Home OS 1337x Browser Bridge"},
    )
    ElementTree.SubElement(caps, "limits", {"max": "60", "default": "20"})
    ElementTree.SubElement(
        caps,
        "registration",
        {"available": "no", "open": "no"},
    )
    searching = ElementTree.SubElement(caps, "searching")
    for name, parameters in (
        ("search", "q"),
        ("tv-search", "q,season,ep"),
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
    for category_id, name, children in (
        (1000, "Console", ()),
        (2000, "Movies", ((2040, "Movies/HD"), (2045, "Movies/UHD"))),
        (3000, "Audio", ()),
        (4000, "PC", ((4050, "PC/Games"),)),
        (5000, "TV", ((5030, "TV/SD"), (5040, "TV/HD"), (5070, "TV/Anime"))),
        (6000, "XXX", ()),
        (7000, "Books", ((7020, "Books/EBook"),)),
        (8000, "Other", ()),
    ):
        category = ElementTree.SubElement(
            categories,
            "category",
            {"id": str(category_id), "name": name},
        )
        for child_id, child_name in children:
            ElementTree.SubElement(
                category,
                "subcat",
                {"id": str(child_id), "name": child_name},
            )
    return ElementTree.tostring(caps, encoding="utf-8", xml_declaration=True)


def build_search_xml(
    results: list[SearchResult],
    *,
    base_url: str,
    api_key: str,
    site_url: str = "https://x1337x.ws",
    offset: int = 0,
    total: int | None = None,
) -> bytes:
    rss = ElementTree.Element("rss", {"version": "2.0"})
    channel = ElementTree.SubElement(rss, "channel")
    _add_text(channel, "title", "Home OS 1337x Browser Bridge")
    _add_text(channel, "description", "Browser-backed Torznab results from 1337x")
    _add_text(channel, "link", base_url)
    ElementTree.SubElement(
        channel,
        f"{{{NEWZNAB_NS}}}response",
        {
            "offset": str(offset),
            "total": str(total if total is not None else len(results)),
        },
    )
    now = datetime.now(UTC)
    for result in results:
        download_query = urlencode({"path": result.detail_path, "apikey": api_key})
        download_url = f"{base_url.rstrip('/')}/download?{download_query}"
        detail_url = f"{site_url.rstrip('/')}{result.detail_path}"
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


def extract_info_hash(magnet_uri: str) -> str:
    values = parse_qs(urlparse(magnet_uri).query).get("xt", [])
    value = next((item for item in values if item.lower().startswith("urn:btih:")), "")
    info_hash = value.rsplit(":", 1)[-1].upper()
    if re.fullmatch(r"[A-F0-9]{40}", info_hash):
        return info_hash
    if re.fullmatch(r"[A-Z2-7]{32}", info_hash):
        try:
            return binascii.hexlify(base64.b32decode(info_hash)).decode().upper()
        except binascii.Error as exc:
            raise ValueError("invalid base32 info hash") from exc
    raise ValueError("magnet URI does not contain a valid BitTorrent v1 info hash")
