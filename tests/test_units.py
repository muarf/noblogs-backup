"""Tests unitaires des fonctions pures de noblogs-backup (sans réseau)."""
from __future__ import annotations

import io
import json
import re
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path

from backup.media import _now_timestamp, relative_dest
from backup.package import human_size
from backup.wxr import generate_wxr, rewrite_media_urls
from backup.html_clean import clean_content
from backup.fidelity import _extract_custom_css


class TestRelativeDest(unittest.TestCase):
    def test_files_path_preserved(self):
        self.assertEqual(
            relative_dest("https://monblog.noblogs.org/files/2024/04/img.jpg"),
            "2024/04/img.jpg",
        )

    def test_uploads_path(self):
        self.assertEqual(
            relative_dest("https://monblog.noblogs.org/uploads/foo/bar.png"),
            "foo/bar.png",
        )

    def test_query_and_fragment_stripped(self):
        self.assertEqual(
            relative_dest("https://monblog.noblogs.org/files/a.png?size=full#x"),
            "a.png",
        )

    def test_bare_basename_fallback(self):
        self.assertEqual(relative_dest("https://cdn.example.org/pic.jpg"), "pic.jpg")

    def test_path_traversal_neutralized(self):
        self.assertEqual(
            relative_dest("https://x.org/files/../../etc/passwd"),
            "etc/passwd",
        )
        self.assertEqual(
            relative_dest("https://x.org/files/%2e%2e/%2e%2e/exploit.php"),
            "%2e%2e/%2e%2e/exploit.php",
        )


class TestHumanSize(unittest.TestCase):
    def test_units(self):
        self.assertEqual(human_size(512), "512.0 o")
        self.assertEqual(human_size(2048), "2.0 Ko")
        self.assertEqual(human_size(3 * 1024 * 1024), "3.0 Mo")
        self.assertEqual(human_size(1024**3), "1.0 Go")


class TestCleanContent(unittest.TestCase):
    def test_cdata_and_entities(self):
        self.assertEqual(clean_content("<![CDATA[a &amp; b]]>"), "a & b")

    def test_curly_quotes_normalized(self):
        self.assertEqual(clean_content("\u201cbonjour\u201d"), '"bonjour"')

    def test_empty(self):
        self.assertEqual(clean_content(""), "")
        self.assertEqual(clean_content(None), "")


class TestRewriteMediaUrls(unittest.TestCase):
    def test_slug_noblogs(self):
        out = rewrite_media_urls(
            '<img src="https://monblog.noblogs.org/files/2024/a/b.jpg">',
            "monblog",
            "https://monblog.noblogs.org",
        )
        self.assertIn("https://monblog.noblogs.org/wp-content/uploads/2024/a/b.jpg", out)

    def test_other_domain_files(self):
        out = rewrite_media_urls(
            '<img src="https://cdn.example.org/files/x.png">',
            "monblog",
            "https://monblog.noblogs.org",
        )
        self.assertIn("wp-content/uploads/x.png", out)

    def test_relative_files(self):
        out = rewrite_media_urls('src="/files/a.png"', "monblog", "https://x.org")
        self.assertIn("wp-content/uploads/a.png", out)

    def test_empty(self):
        self.assertEqual(rewrite_media_urls("", "s", "u"), "")


class TestWxr(unittest.TestCase):
    def _posts(self):
        return [
            {"id": 1002, "title": "A", "link": "https://x.noblogs.org/post/a",
             "guid": "https://x.noblogs.org/?p=1002", "cats": ["News"],
             "date": "Wed, 01 Jan 2026 10:00:00 +0200", "content": "<p>hi</p>"},
            {"id": None, "title": "B", "link": "https://x.noblogs.org/2026/01/b",
             "guid": "https://x.noblogs.org/2026/01/b", "cats": [],
             "date": "", "content": "<p>ho</p>"},
        ]

    def test_xml_is_valid(self):
        xml = generate_wxr("x", "X", "https://x.noblogs.org", self._posts(), [], language="fr-FR")
        root = ET.fromstring(xml)
        items = root.findall(".//item")
        self.assertEqual(len(items), 2)
        wp = "{http://wordpress.org/export/1.2/}"
        types = [it.find(f"{wp}post_type").text for it in items]
        self.assertEqual(types, ["post", "post"])

    def test_post_ids_unique(self):
        posts = self._posts() + [
            {"id": 1003, "title": "C", "link": "https://x.noblogs.org/post/c",
             "guid": "https://x.noblogs.org/?p=1003", "cats": []},
        ]
        xml = generate_wxr("x", "X", "https://x.noblogs.org", posts, [], language="fr-FR")
        ids = re.findall(r"<wp:post_id>(\d+)</wp:post_id>", xml)
        self.assertEqual(len(ids), len(set(ids)))

    def test_date_gmt_conversion(self):
        xml = generate_wxr("x", "X", "https://x.noblogs.org", self._posts(), [], language="fr-FR")
        local = re.search(r"<wp:post_date><!\[CDATA\[(.*?)\]\]>", xml).group(1)
        gmt = re.search(r"<wp:post_date_gmt><!\[CDATA\[(.*?)\]\]>", xml).group(1)
        self.assertEqual(local, "2026-01-01 10:00:00")
        self.assertEqual(gmt, "2026-01-01 08:00:00")

    def test_pages_use_real_dates(self):
        posts = [{"id": None, "title": "A", "link": "https://x.noblogs.org/post/a",
                  "guid": "https://x.noblogs.org/post/a", "cats": [],
                  "date": "Tue, 02 Feb 2021 09:00:00 +0000", "content": ""}]
        pages = [{"title": "P", "slug": "p", "url": "https://x.noblogs.org/p",
                  "date": "2021-05-01T14:22:00", "content": "<p>page</p>"}]
        xml = generate_wxr("x", "X", "https://x.noblogs.org", posts, pages, language="fr-FR")
        self.assertIn("2021-05-01 14:22:00", xml)
        self.assertIn("2021-02-02 09:00:00", xml)

    def test_media_urls_rewritten_in_items(self):
        posts = [{"id": None, "title": "A", "link": "https://x.noblogs.org/post/a",
                  "guid": "https://x.noblogs.org/post/a", "cats": [],
                  "content": '<img src="https://x.noblogs.org/files/2024/img.jpg">'}]
        xml = generate_wxr("x", "X", "https://x.noblogs.org", posts, [], language="fr-FR")
        self.assertIn("https://x.noblogs.org/wp-content/uploads/2024/img.jpg", xml)

    def test_attachments_generated(self):
        attachments = [
            {"url": "https://x.noblogs.org/files/2024/img.jpg", "path": "2024/img.jpg", "title": "img.jpg"},
            {"url": "https://x.noblogs.org/files/2024/doc.pdf", "path": "2024/doc.pdf", "title": "doc.pdf"},
        ]
        xml = generate_wxr("x", "X", "https://x.noblogs.org", self._posts(), [],
                           attachments=attachments, language="fr-FR")
        root = ET.fromstring(xml)
        wp = "{http://wordpress.org/export/1.2/}"
        items = root.findall(".//item")
        atts = [it for it in items if it.find(f"{wp}post_type").text == "attachment"]
        self.assertEqual(len(atts), 2)
        urls = sorted(a.find(f"{wp}attachment_url").text for a in atts)
        self.assertEqual(urls, [
            "https://x.noblogs.org/files/2024/doc.pdf",
            "https://x.noblogs.org/files/2024/img.jpg",
        ])

    def test_base_blog_url_present(self):
        xml = generate_wxr("x", "X", "https://x.noblogs.org", self._posts(), [], language="fr-FR")
        self.assertIn("<wp:base_blog_url>https://x.noblogs.org</wp:base_blog_url>", xml)

    def test_post_status_publish(self):
        xml = generate_wxr("x", "X", "https://x.noblogs.org", self._posts(), [], language="fr-FR")
        statuses = re.findall(r"<wp:status><!\[CDATA\[(.*?)\]\]></wp:status>", xml)
        self.assertTrue(statuses, "les items doivent porter <wp:status>")
        self.assertTrue(all(s == "publish" for s in statuses), statuses)
        self.assertNotIn("<wp:post_status>", xml)


class TestScrapePagesPagination(unittest.TestCase):
    def test_pagination_loop(self):
        from unittest.mock import patch
        from backup.scraper import NoblogsScraper

        one = {"title": {"rendered": "P"}, "link": "/p", "slug": "p",
               "content": {"rendered": "<p>a</p>"}, "date": "2021-01-01T00:00:00"}

        calls: list[str] = []

        def fake(url: str):
            calls.append(url)
            if "per_page=100&page=1" in url:
                return 200, json.dumps([one] * 100).encode("utf-8")
            if "per_page=100&page=2" in url:
                return 200, json.dumps([one]).encode("utf-8")
            return 0, b""

        with patch("backup.scraper.fetch_url", side_effect=fake):
            pages = NoblogsScraper("x").scrape_pages()

        self.assertEqual(len(pages), 101)
        self.assertEqual(len(calls), 2)

    def test_stops_when_less_than_batch(self):
        from unittest.mock import patch
        from backup.scraper import NoblogsScraper

        one = {"title": {"rendered": "P"}, "link": "/p", "slug": "p",
               "content": {"rendered": "<p>a</p>"}, "date": ""}

        calls: list[str] = []

        def fake(url: str):
            calls.append(url)
            if "per_page=100&page=1" in url:
                return 200, json.dumps([one]).encode("utf-8")
            return 0, b""

        with patch("backup.scraper.fetch_url", side_effect=fake):
            pages = NoblogsScraper("x").scrape_pages()

        self.assertEqual(len(pages), 1)
        self.assertEqual(len(calls), 1)


class TestFidelityCssFilter(unittest.TestCase):
    def test_block_inline_css_removed(self):
        html = (
            '<style id="wp-block-paragraph-inline-css">'
            ".is-small-text{font-size:.875em}"
            "/*# sourceURL=/wp-includes/blocks/p/style.min.css */"
            "</style>"
            '<style id="wp-custom-css">.post{margin:0}</style>'
        )
        css = _extract_custom_css(html, "monblog")
        self.assertIn(".post{margin:0}", css)
        self.assertNotIn("is-small-text", css)
        self.assertNotIn("sourceURL", css)


class TestMediaTimestamp(unittest.TestCase):
    def test_now_timestamp_format_and_dynamic(self):
        from unittest.mock import patch
        with patch("backup.media.datetime") as mock_dt:
            mock_dt.now.return_value = __import__("datetime").datetime(2026, 3, 5, 7, 8, 9)
            self.assertEqual(_now_timestamp(), "20260305070809")
        self.assertRegex(_now_timestamp(), r"^\d{14}$")


class TestRestorableHelpers(unittest.TestCase):
    def test_restore_dir_default(self):
        import importlib
        import backup.wizard as wz
        self.assertEqual(wz._restore_dir("monblog"), wz.RESTORE_ROOT / "monblog")

    def test_restore_dir_env_override(self):
        import os
        import backup.wizard as wz
        old = os.environ.get("NOBLOGS_RESTORE")
        try:
            os.environ["NOBLOGS_RESTORE"] = "/opt/noblogs-sites"
            self.assertEqual(str(wz._restore_dir("a")), "/opt/noblogs-sites/a")
        finally:
            if old is None:
                os.environ.pop("NOBLOGS_RESTORE", None)
            else:
                os.environ["NOBLOGS_RESTORE"] = old

    def test_extract_to(self):
        import io
        import tempfile
        import zipfile
        import backup.wizard as wz
        with tempfile.TemporaryDirectory() as td:
            zip_path = Path(td) / "x.zip"
            with zipfile.ZipFile(zip_path, "w") as zf:
                zf.writestr("metadata.json", '{"slug": "fixture"}')
                zf.writestr("wordpress-export.xml", "<rss></rss>")
            dst = Path(td) / "restores" / "fixture"
            out = wz._extract_to(zip_path, dst)
            self.assertEqual(out, dst)
            self.assertTrue((dst / "metadata.json").exists())
            self.assertTrue((dst / "wordpress-export.xml").exists())
            self.assertEqual(json.loads((dst / "metadata.json").read_text())["slug"], "fixture")

    def test_with_temp_extract_cleans_up(self):
        import tempfile
        import zipfile
        import backup.wizard as wz
        seen: list[Path] = []
        with tempfile.TemporaryDirectory() as td:
            zip_path = Path(td) / "x.zip"
            with zipfile.ZipFile(zip_path, "w") as zf:
                zf.writestr("metadata.json", "{}")
            wz._with_temp_extract(zip_path, lambda ex: seen.append(Path(ex)))
            extracted = seen[0]
            self.assertTrue(extracted.name.startswith("noblogs-restore."))
            self.assertFalse(Path(extracted).exists())


class TestStore(unittest.TestCase):
    """Store local (thèmes/plugins/mu-plugins) + embarquement dans le ZIP."""

    def _fake_store(self):
        """Construit un store factice dans un dossier temporaire."""
        import tempfile
        td = Path(tempfile.mkdtemp(prefix="noblogs-store-test."))
        self.addCleanup(__import__("shutil").rmtree, td, ignore_errors=True)
        themes = td / "themes" / "fixturetheme"
        themes.mkdir(parents=True)
        (themes / "style.css").write_text("/* theme */")
        (themes / "index.php").write_text("<?php /* template */ ?>")
        (td / "themes" / "incomplete").mkdir(parents=True)
        (td / "themes" / "incomplete" / "style.css").write_text("/* only css */")
        pl = td / "plugins" / "event-organiser"
        pl.mkdir(parents=True)
        (pl / "event-organiser.php").write_text("<?php /* plugin */ ?>")
        mu = td / "mu-plugins"
        mu.mkdir()
        (mu / "force-layout-balance.php").write_text("<?php /* mu */ ?>")
        return td

    def test_has_template_and_resolve(self):
        import os
        import backup.store as st
        td = self._fake_store()
        old = os.environ.get("NOBLOGS_STORE")
        try:
            os.environ["NOBLOGS_STORE"] = str(td)
            self.assertTrue(st.has_template("fixturetheme"))
            self.assertFalse(st.has_template("incomplete"))
            dest = Path(td) / "out"
            out = st.resolve_theme("fixturetheme", dest)
            self.assertTrue((dest / "fixturetheme" / "style.css").exists())
            self.assertTrue((dest / "fixturetheme" / "index.php").exists())
            self.assertIsNone(st.resolve_theme("inconnu", dest))
            self.assertEqual(st.plugins_list(), ["event-organiser"])
            self.assertEqual(st.mu_plugins_list(), ["force-layout-balance.php"])
        finally:
            if old is None:
                os.environ.pop("NOBLOGS_STORE", None)
            else:
                os.environ["NOBLOGS_STORE"] = old

    def test_upgrade_zip_adds_store_content(self):
        import os
        import tempfile
        import zipfile
        import backup.store as st
        td = self._fake_store()
        old = os.environ.get("NOBLOGS_STORE")
        try:
            os.environ["NOBLOGS_STORE"] = str(td)
            with tempfile.TemporaryDirectory() as tmp:
                zip_path = Path(tmp) / "b.zip"
                with zipfile.ZipFile(zip_path, "w") as zf:
                    zf.writestr("wordpress-export.xml", "<rss></rss>")
                    zf.writestr("metadata.json",
                                json.dumps({"slug": "b", "theme": "fixturetheme"}))
                res = st.upgrade_zip(zip_path)
                self.assertTrue(res["theme_complete"])
                self.assertEqual(res["plugins"], ["event-organiser"])
                self.assertEqual(res["mu_plugins"], ["force-layout-balance.php"])
                with zipfile.ZipFile(zip_path) as zf:
                    names = set(zf.namelist())
                    self.assertIn("theme/fixturetheme/index.php", names)
                    self.assertIn("plugins/event-organiser/event-organiser.php", names)
                    self.assertIn("mu-plugins/force-layout-balance.php", names)
                    self.assertIn("wordpress-export.xml", names)
                    meta = json.loads(zf.read("metadata.json"))
                    self.assertTrue(meta["theme_complete"])
        finally:
            if old is None:
                os.environ.pop("NOBLOGS_STORE", None)
            else:
                os.environ["NOBLOGS_STORE"] = old


class TestPackageBundles(unittest.TestCase):
    def test_package_bundles_theme_plugins_mu(self):
        import io
        import tempfile
        import zipfile
        from backup.package import package_backup

        with tempfile.TemporaryDirectory() as tmp:
            td = Path(tmp)
            exports = td / "backups"
            wxr = td / "x.xml"
            wxr.write_text("<rss></rss>")
            uploads = td / "uploads"
            uploads.mkdir()
            theme_dir = td / "fixturetheme"
            theme_dir.mkdir()
            (theme_dir / "style.css").write_text("/* */")
            (theme_dir / "index.php").write_text("<?php ?>")
            plugins = td / "plugins"
            p = plugins / "akismet"
            p.mkdir(parents=True)
            (p / "akismet.php").write_text("<?php ?>")
            mu = td / "mu-plugins"
            mu.mkdir()
            (mu / "m.php").write_text("<?php ?>")

            zip_path = package_backup(
                slug="s", exports_dir=exports, wxr_path=wxr, uploads_dir=uploads,
                original_url="https://s.noblogs.org", theme="fixturetheme",
                theme_dir=theme_dir, plugins_dir=plugins, mu_plugins_dir=mu,
            )
            with zipfile.ZipFile(zip_path) as zf:
                names = set(zf.namelist())
                self.assertIn("theme/fixturetheme/index.php", names)
                self.assertIn("plugins/akismet/akismet.php", names)
                self.assertIn("mu-plugins/m.php", names)
                meta = json.loads(zf.read("metadata.json"))
                self.assertTrue(meta["theme_complete"])
                self.assertEqual(meta["plugins"], ["akismet"])
                self.assertEqual(meta["mu_plugins"], ["m.php"])

    def test_theme_without_template_marked_incomplete(self):
        import tempfile
        import zipfile
        from backup.package import package_backup

        with tempfile.TemporaryDirectory() as tmp:
            td = Path(tmp)
            exports = td / "backups"
            wxr = td / "x.xml"
            wxr.write_text("<rss></rss>")
            uploads = td / "uploads"
            uploads.mkdir()
            theme_dir = td / "broken"
            theme_dir.mkdir()
            (theme_dir / "style.css").write_text("/* */")  # pas de template → incomplet

            zip_path = package_backup(
                slug="s", exports_dir=exports, wxr_path=wxr, uploads_dir=uploads,
                original_url="https://s.noblogs.org", theme="broken", theme_dir=theme_dir,
            )
            with zipfile.ZipFile(zip_path) as zf:
                meta = json.loads(zf.read("metadata.json"))
                self.assertFalse(meta["theme_complete"])


if __name__ == "__main__":
    unittest.main()