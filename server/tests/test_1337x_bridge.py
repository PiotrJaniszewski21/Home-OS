import sys
import unittest
from pathlib import Path
from xml.etree import ElementTree

ROOT_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT_DIR))

from services.torznab_1337x_bridge.core import (
    NEWZNAB_NS,
    TORZNAB_NS,
    SearchResult,
    build_caps_xml,
    build_search_query,
    build_search_xml,
    extract_info_hash,
    parse_search_results,
    parse_size,
    search_path,
)

SAMPLE_HTML = """
<table class="table-list"><tbody>
  <tr>
    <td class="coll-1 name">
      <a href="/sub/42/0/">Movies/HD</a>
      <a href="/torrent/1234/Example-Release-2026/">Example Release 2026</a>
    </td>
    <td class="coll-2 seeds">1,234</td>
    <td class="coll-3 leeches">56</td>
    <td class="coll-date">10am Jul. 30th</td>
    <td class="coll-4 size">1.5 GB</td>
    <td class="coll-5 user">Uploader</td>
  </tr>
</tbody></table>
"""


class BridgeCoreTests(unittest.TestCase):
    def test_parses_1337x_result_table(self):
        results = parse_search_results(SAMPLE_HTML)

        self.assertEqual(len(results), 1)
        self.assertEqual(results[0].title, "Example Release 2026")
        self.assertEqual(results[0].detail_path, "/torrent/1234/Example-Release-2026/")
        self.assertEqual(results[0].category, 2040)
        self.assertEqual(results[0].size, int(1.5 * 1024**3))
        self.assertEqual(results[0].seeders, 1234)
        self.assertEqual(results[0].leechers, 56)
        self.assertEqual(results[0].uploader, "Uploader")

    def test_parse_size_supports_binary_units(self):
        self.assertEqual(parse_size("750 MB"), 750 * 1024**2)
        self.assertEqual(parse_size("2.25 TB"), int(2.25 * 1024**4))
        self.assertEqual(parse_size("unknown"), 0)
        self.assertEqual(parse_size(". GB"), 0)

    def test_builds_search_terms_and_encoded_path(self):
        self.assertEqual(
            build_search_query({"q": "Example Show", "season": "2", "ep": "3"}),
            "Example Show S02E03",
        )
        self.assertEqual(search_path("A show/2026", 2), "/search/A%20show%2F2026/2/")

    def test_caps_advertise_torznab_search_modes(self):
        root = ElementTree.fromstring(build_caps_xml())

        self.assertEqual(root.tag, "caps")
        self.assertEqual(root.find("searching/tv-search").attrib["available"], "yes")
        self.assertIsNotNone(root.find("categories/category[@id='5000']"))

    def test_search_feed_contains_download_and_torznab_attributes(self):
        result = SearchResult(
            title="Example",
            detail_path="/torrent/1234/Example/",
            category=2040,
            size=1024,
            seeders=10,
            leechers=2,
        )
        root = ElementTree.fromstring(
            build_search_xml(
                [result],
                base_url="http://127.0.0.1:8787",
                api_key="secret",
                site_url="https://x1337x.eu",
            )
        )

        item = root.find("./channel/item")
        self.assertIsNotNone(item)
        self.assertIn("apikey=secret", item.findtext("link"))
        self.assertEqual(
            item.findtext("comments"),
            "https://x1337x.eu/torrent/1234/Example/",
        )
        response = root.find(f"./channel/{{{NEWZNAB_NS}}}response")
        self.assertEqual(response.attrib["total"], "1")
        attributes = {
            child.attrib["name"]: child.attrib["value"]
            for child in item.findall(f"{{{TORZNAB_NS}}}attr")
        }
        self.assertEqual(attributes["seeders"], "10")
        self.assertEqual(attributes["peers"], "12")

    def test_extracts_hex_and_base32_info_hashes(self):
        hex_hash = "0123456789ABCDEF0123456789ABCDEF01234567"
        self.assertEqual(
            extract_info_hash(f"magnet:?xt=urn:btih:{hex_hash.lower()}"),
            hex_hash,
        )
        self.assertEqual(
            extract_info_hash("magnet:?xt=urn:btih:AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"),
            "0000000000000000000000000000000000000000",
        )

    def test_rejects_invalid_info_hash(self):
        with self.assertRaises(ValueError):
            extract_info_hash("magnet:?xt=urn:btih:not-a-hash")


if __name__ == "__main__":
    unittest.main()
