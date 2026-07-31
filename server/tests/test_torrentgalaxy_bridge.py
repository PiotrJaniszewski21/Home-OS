import sys
import unittest
from datetime import UTC, datetime
from pathlib import Path
from xml.etree import ElementTree

ROOT_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT_DIR / "services" / "torznab_1337x_bridge"))

from core import NEWZNAB_NS, TORZNAB_NS
from tgx_core import (
    TorrentGalaxyResult,
    build_torrentgalaxy_caps_xml,
    build_torrentgalaxy_search_xml,
    parse_torrentgalaxy_results,
    torrentgalaxy_category_path,
    torrentgalaxy_search_path,
)

SAMPLE_HTML = """
<div class="tgxtablerow txlight">
  <div class="tgxtablecell shrink">
    <a href="/get-posts/category:Apps:time:10D"><small>Apps</small></a>
  </div>
  <div class="tgxtablecell">verified</div>
  <div class="tgxtablecell">English</div>
  <div class="tgxtablecell">
    <a title="Ubuntu Example 2026"
       href="/post-detail/8a6ee9/ubuntu-example-2026/">Ubuntu Example 2026</a>
  </div>
  <div class="tgxtablecell">download</div>
  <div class="tgxtablecell">-</div>
  <div class="tgxtablecell">
    <a class="username" href="/get-posts/user:example/">ExampleUploader</a>
  </div>
  <div class="tgxtablecell"><span class="badge badge-secondary">5.8 GB</span></div>
  <div class="tgxtablecell">0</div>
  <div class="tgxtablecell">10</div>
  <div class="tgxtablecell">
    <span title="Seeders/Leechers">[<font><b>1,234</b></font>/<font><b>56</b></font>]</span>
  </div>
  <div class="tgxtablecell">
    <small class="added-date" data-timestamp="2026-07-30T09:15:00+00:00"></small>
  </div>
</div>
"""


class TorrentGalaxyBridgeTests(unittest.TestCase):
    def test_parses_torrentgalaxy_result_rows(self):
        results = parse_torrentgalaxy_results(SAMPLE_HTML)

        self.assertEqual(len(results), 1)
        self.assertEqual(results[0].title, "Ubuntu Example 2026")
        self.assertEqual(
            results[0].detail_path,
            "/post-detail/8a6ee9/ubuntu-example-2026/",
        )
        self.assertEqual(results[0].category, 4000)
        self.assertEqual(results[0].size, int(5.8 * 1024**3))
        self.assertEqual(results[0].seeders, 1234)
        self.assertEqual(results[0].leechers, 56)
        self.assertEqual(results[0].uploader, "ExampleUploader")
        self.assertEqual(
            results[0].published,
            datetime(2026, 7, 30, 9, 15, tzinfo=UTC),
        )

    def test_ignores_rows_without_a_valid_detail_path(self):
        document = SAMPLE_HTML.replace(
            "/post-detail/8a6ee9/ubuntu-example-2026/",
            "https://example.com/not-a-torrent",
        )

        self.assertEqual(parse_torrentgalaxy_results(document), [])

    def test_builds_encoded_search_paths_and_pages(self):
        self.assertEqual(
            torrentgalaxy_search_path("Ubuntu 24.04/arm"),
            "/get-posts/keywords:Ubuntu%2024.04%2Farm/",
        )
        self.assertEqual(
            torrentgalaxy_search_path("Ubuntu", 2),
            "/get-posts/keywords:Ubuntu/?page=2",
        )
        self.assertEqual(
            torrentgalaxy_category_path(2000),
            "/get-posts/category:Movies:time:10D/",
        )
        self.assertEqual(
            torrentgalaxy_category_path(2040, 1),
            "/get-posts/category:Movies:time:10D/?page=1",
        )

    def test_caps_advertise_search_modes(self):
        root = ElementTree.fromstring(build_torrentgalaxy_caps_xml())

        self.assertEqual(
            root.find("server").attrib["title"],
            "Home OS TorrentGalaxy Browser Bridge",
        )
        self.assertEqual(root.find("searching/tv-search").attrib["available"], "yes")
        self.assertIsNotNone(root.find("categories/category[@id='5080']"))

    def test_search_feed_uses_lazy_download_endpoint(self):
        result = TorrentGalaxyResult(
            title="Ubuntu Example",
            detail_path="/post-detail/8a6ee9/ubuntu-example/",
            category=4000,
            size=1024,
            seeders=10,
            leechers=2,
        )
        root = ElementTree.fromstring(
            build_torrentgalaxy_search_xml(
                [result],
                base_url="http://127.0.0.1:8787",
                api_key="secret",
                site_url="https://torrentgalaxy.one",
            )
        )

        item = root.find("./channel/item")
        self.assertIn("/torrentgalaxy/download?", item.findtext("link"))
        self.assertIn("apikey=secret", item.findtext("link"))
        self.assertEqual(
            item.findtext("comments"),
            "https://torrentgalaxy.one/post-detail/8a6ee9/ubuntu-example/",
        )
        response = root.find(f"./channel/{{{NEWZNAB_NS}}}response")
        self.assertEqual(response.attrib["total"], "1")
        attributes = {
            child.attrib["name"]: child.attrib["value"]
            for child in item.findall(f"{{{TORZNAB_NS}}}attr")
        }
        self.assertEqual(attributes["seeders"], "10")
        self.assertEqual(attributes["peers"], "12")


if __name__ == "__main__":
    unittest.main()
