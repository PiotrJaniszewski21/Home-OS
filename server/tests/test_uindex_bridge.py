import sys
import unittest
from pathlib import Path
from xml.etree import ElementTree

ROOT_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT_DIR / "services" / "torznab_1337x_bridge"))

from core import NEWZNAB_NS, TORZNAB_NS
from uindex_core import (
    UindexResult,
    build_uindex_caps_xml,
    build_uindex_search_xml,
    parse_uindex_results,
    uindex_search_path,
)

SAMPLE_HTML = """
<table class="sr-table"><tbody>
  <tr>
    <td><a href="/search.php?c=5">Apps</a></td>
    <td><a href="/details.php?id=1234">Ubuntu Example 2026</a></td>
    <td>
      <a href="magnet:?xt=urn:btih:0123456789ABCDEF0123456789ABCDEF01234567&amp;dn=Ubuntu">Magnet</a>
    </td>
    <td class="sr-col-uploaded">2026-07-30</td>
    <td class="sr-col-size">2.5 GB</td>
    <td class="sr-col-seeders">1,234</td>
    <td class="sr-col-leechers">56</td>
  </tr>
</tbody></table>
"""


class UindexBridgeTests(unittest.TestCase):
    def test_parses_uindex_result_table(self):
        results = parse_uindex_results(SAMPLE_HTML)

        self.assertEqual(len(results), 1)
        self.assertEqual(results[0].title, "Ubuntu Example 2026")
        self.assertEqual(results[0].detail_path, "/details.php?id=1234")
        self.assertEqual(results[0].category, 4000)
        self.assertEqual(results[0].size, int(2.5 * 1024**3))
        self.assertEqual(results[0].seeders, 1234)
        self.assertEqual(results[0].leechers, 56)
        self.assertIn("dn=Ubuntu", results[0].magnet)

    def test_ignores_rows_without_valid_magnet(self):
        document = SAMPLE_HTML.replace(
            "magnet:?xt=urn:btih:0123456789ABCDEF0123456789ABCDEF01234567&amp;dn=Ubuntu",
            "https://example.com/not-a-magnet",
        )

        self.assertEqual(parse_uindex_results(document), [])

    def test_builds_encoded_search_path(self):
        self.assertEqual(
            uindex_search_path("Ubuntu 24.04/arm"),
            "/search.php?search=Ubuntu+24.04%2Farm&c=0",
        )

    def test_caps_advertise_search_modes(self):
        root = ElementTree.fromstring(build_uindex_caps_xml())

        self.assertEqual(root.find("server").attrib["title"], "Home OS Uindex Browser Bridge")
        self.assertEqual(root.find("searching/tv-search").attrib["available"], "yes")

    def test_search_feed_contains_magnet_and_attributes(self):
        result = UindexResult(
            title="Ubuntu Example",
            detail_path="/details.php?id=1234",
            magnet="magnet:?xt=urn:btih:0123456789ABCDEF0123456789ABCDEF01234567",
            category=4000,
            size=1024,
            seeders=10,
            leechers=2,
        )
        root = ElementTree.fromstring(
            build_uindex_search_xml(
                [result],
                site_url="https://uindex.org",
            )
        )

        item = root.find("./channel/item")
        self.assertTrue(item.findtext("link").startswith("magnet:?"))
        self.assertEqual(
            item.findtext("comments"),
            "https://uindex.org/details.php?id=1234",
        )
        response = root.find(f"./channel/{{{NEWZNAB_NS}}}response")
        self.assertEqual(response.attrib["total"], "1")
        attributes = {
            child.attrib["name"]: child.attrib["value"]
            for child in item.findall(f"{{{TORZNAB_NS}}}attr")
        }
        self.assertEqual(attributes["seeders"], "10")
        self.assertTrue(attributes["magneturl"].startswith("magnet:?"))


if __name__ == "__main__":
    unittest.main()
