"""Tests du convertisseur WXR → dépôt Hugo statique (hors-ligne)."""
from __future__ import annotations

import io
import json
import shutil
import tempfile
import unittest
import zipfile
from pathlib import Path

from backup import hugo as h


def fixture_extracted(tmp: Path, profile: str = "montheme") -> Path:
    """Construit un dossier « ZIP décompressé » manuel : WXR + uploads + theme + fidelity."""
    extracted = tmp / "x"
    (extracted / "uploads" / "2026" / "01").mkdir(parents=True)
    (extracted / "uploads" / "2026" / "01" / "pic.jpg").write_bytes(b"\xff\xd8" + b"0" * 200)
    (extracted / "uploads" / "2025" / "12").mkdir(parents=True)
    (extracted / "uploads" / "2025" / "12" / "paper.pdf").write_bytes(b"%PDF" + b"0" * 200)

    theme = extracted / "theme" / profile
    theme.mkdir(parents=True)
    (theme / "style.css").write_text("body { color: #333; }")
    (theme / "images").mkdir()
    (theme / "images" / "logo.png").write_bytes(b"\x89PNG" + b"0" * 100)

    wxr = f"""<?xml version="1.0"?>
<rss version="2.0"
     xmlns:dc="http://purl.org/dc/elements/1.1/"
     xmlns:content="http://purl.org/rss/1.0/modules/content/"
     xmlns:excerpt="http://wordpress.org/export/1.2/excerpt/"
     xmlns:wp="http://wordpress.org/export/1.2/">
<channel>
  <title><![CDATA[Mon Blog]]></title>
  <link>https://monblog.noblogs.org/</link>
  <language>fr-FR</language>
  <item>
    <title><![CDATA[p'tits fils d'agitation]]></title>
    <link>https://monblog.noblogs.org/post/2026/01/05/premier/</link>
    <pubDate>Mon, 05 Jan 2026 12:00:00 +0000</pubDate>
    <dc:creator><![CDATA[monblog]]></dc:creator>
    <content:encoded><![CDATA[<p>Hello <img src="https://monblog.noblogs.org/wp-content/uploads/2026/01/pic.jpg"></p>]]></content:encoded>
    <wp:post_id>101</wp:post_id>
    <wp:post_date><![CDATA[2026-01-05 13:00:00]]></wp:post_date>
    <wp:status><![CDATA[publish]]></wp:status>
    <wp:post_name><![CDATA[premier]]></wp:post_name>
    <wp:post_type><![CDATA[post]]></wp:post_type>
    <category domain="category" nicename="general"><![CDATA[General]]></category>
  </item>
  <item>
    <title><![CDATA[Pdf]]></title>
    <link>https://monblog.noblogs.org/2026/01/06/pdf/</link>
    <pubDate>Tue, 06 Jan 2026 12:00:00 +0000</pubDate>
    <dc:creator><![CDATA[monblog]]></dc:creator>
    <content:encoded><![CDATA[<p><a href="https://monblog.noblogs.org/wp-content/uploads/2025/12/paper.pdf">PDF</a></p>]]></content:encoded>
    <wp:post_id>102</wp:post_id>
    <wp:post_date><![CDATA[2026-01-06 13:00:00]]></wp:post_date>
    <wp:status><![CDATA[publish]]></wp:status>
    <wp:post_name><![CDATA[pdf]]></wp:post_name>
    <wp:post_type><![CDATA[post]]></wp:post_type>
  </item>
  <item>
    <title><![CDATA[À propos]]></title>
    <link>https://monblog.noblogs.org/a-propos/</link>
    <dc:creator><![CDATA[monblog]]></dc:creator>
    <content:encoded><![CDATA[<p>Page statique.</p>]]></content:encoded>
    <wp:post_id>200</wp:post_id>
    <wp:post_date><![CDATA[2021-05-01 14:22:00]]></wp:post_date>
    <wp:status><![CDATA[publish]]></wp:status>
    <wp:post_name><![CDATA[a-propos]]></wp:post_name>
    <wp:post_type><![CDATA[page]]></wp:post_type>
  </item>
</channel>
</rss>"""
    (extracted / "wordpress-export.xml").write_text(wxr, encoding="utf-8")

    (extracted / "fidelity.json").write_text(json.dumps({
        "theme": profile,
        "title": "p'tits fils — Mon Blog",
        "tagline": "la tagline",
        "custom_css": ".post { margin: 0; }",
        "menu_items": [
            {"title": "Accueil", "href": "/"},
            {"title": "À propos", "href": "/a-propos", "children": [{"title": "Contact", "href": "/contact"}]},
        ],
        "sidebars": {"sidebar-1": [
            {"type": "search", "title": ""},
            {"type": "custom_html", "title": "Widget", "content": "<p>Bonjour {{ monde }}</p>"},
        ]},
        "background": {},
        "theme_colors": {},
    }, ensure_ascii=False), encoding="utf-8")
    return extracted


class TestParse(unittest.TestCase):
    def test_parse_date(self):
        self.assertTrue(h.parse_date("Mon, 05 Jan 2026 12:00:00 +0000").startswith("2026-01-05"))
        self.assertEqual(h.parse_date(""), "")
        self.assertEqual(h.parse_date("n'importe quoi"), "")

    def test_detect_permalink(self):
        self.assertEqual(h.detect_permalink(["https://x.noblogs.org/post/2026/01/05/a/"]),
                         "post/:year/:month/:day/:slug/")
        self.assertEqual(h.detect_permalink(["https://x.noblogs.org/2026/01/05/a/"]),
                         ":year/:month/:day/:slug/")
        self.assertEqual(h.detect_permalink(["https://x.noblogs.org/post/a/"]),
                         "post/:slug/")
        self.assertEqual(h.detect_permalink(["https://x.noblogs.org/foo/"]),
                         "posts/:slug/")

    def test_slugify(self):
        self.assertEqual(h._slugify("À propos déjà vu"), "a-propos-deja-vu")
        self.assertEqual(h._slugify(""), "")

    def test_clean_html_for_markdown(self):
        self.assertEqual(h.clean_html_for_markdown("<p>\n  toto\n</p>"), "<p>\ntoto\n</p>")
        self.assertEqual(h.clean_html_for_markdown("<pre>\n  kk\n</pre>"), "<pre>\n  kk\n</pre>")

    def test_yaml_front_escapes_quotes(self):
        fm = h.yaml_front("p'tits \"bon\"", "2026-01-05T00:00:00+00:00", "premier",
                          ["General"], [], "monblog", "", False)
        self.assertIn('title: "p\'tits \\"bon\\""', fm)
        self.assertIn("categories:", fm)
        self.assertIn('draft: false', fm)

    def test_render_front_aliases(self):
        it = {"title": "A", "date": "2026-01-05T00:00:00+00:00", "slug": "a",
              "link": "https://x.noblogs.org/post/2026/01/05/a/", "categories": [],
              "tags": [], "author": "", "excerpt": "", "status": "publish"}
        fm = h.render_front(it)
        self.assertIn('aliases:', fm)
        self.assertIn("/post/a/", fm)
        self.assertIn("/post/2026/01/05/a/", fm)


class TestAssetMapper(unittest.TestCase):
    def test_rewrites_existing(self):
        with tempfile.TemporaryDirectory() as td:
            up = Path(td) / "uploads"
            (up / "2026/01").mkdir(parents=True)
            (up / "2026/01/pic.jpg").write_bytes(b"x" * 10)
            m = h.AssetMapper(up)
            out = m.rewrite('<img src="https://b.noblogs.org/wp-content/uploads/2026/01/pic.jpg">')
            self.assertIn('src="/images/2026/01/pic.jpg"', out)
            self.assertEqual(m.total, 1)

    def test_leaves_missing(self):
        with tempfile.TemporaryDirectory() as td:
            up = Path(td) / "uploads"
            up.mkdir()
            m = h.AssetMapper(up)
            out = m.rewrite('<img src="https://b.noblogs.org/wp-content/uploads/nope.jpg">')
            self.assertIn("https://b.noblogs.org/wp-content/uploads/nope.jpg", out)

    def test_files_path(self):
        with tempfile.TemporaryDirectory() as td:
            up = Path(td) / "uploads"
            up.mkdir()
            (up / "a.png").write_bytes(b"x" * 10)
            m = h.AssetMapper(up)
            out = m.rewrite('<a href="https://b.noblogs.org/files/a.png">l</a>')
            self.assertIn('href="/images/a.png"', out)


class TestConfigAndMenu(unittest.TestCase):
    def test_make_config_title_apostrophe(self):
        cfg = h.make_config("monblog", "p'tits fils d'agitation", "", "fr-fr", [],
                            "http://localhost:8092", profile="montheme", extras=True,
                            menu_items=[], sidebar_widgets=[], permalink_page=":slug/")
        self.assertIn("title = \"p'tits fils d'agitation\"", cfg)
        self.assertIn('baseURL = "http://localhost:8092/"', cfg)
        self.assertIn('profile = "montheme"', cfg)
        self.assertIn('themeExtras = ["/css/blog-custom.css"]', cfg)
        self.assertIn('theme = "nblogs"', cfg)

    def test_menu_lines_flattens_children(self):
        items = [
            {"title": "Accueil", "href": "/"},
            {"title": "À propos", "href": "/a-propos", "children": [{"title": "Contact", "href": "/contact"}]},
        ]
        lines = h.menu_lines(items)
        self.assertEqual(lines.count("[[menu.main]]"), 3)
        self.assertIn('name = "Contact"', lines)

    def test_sidebar_partial_escapes_braces(self):
        sb = h._sidebar_partial("monblog", {"sidebar-1": [
            {"type": "custom_html", "title": "W", "content": "<p>{{ var }}</p>"},
        ]})
        # on échappe ces moustaches : elles ne doivent pas rester brutes dans le partial
        self.assertNotIn("<p>{{ var }}</p>", sb)
        self.assertIn("<h3 class='widget-title'>W</h3>", sb)

    def test_sidebar_partial_none_when_empty(self):
        self.assertIsNone(h._sidebar_partial("monblog", {}))
        self.assertIsNone(h._sidebar_partial("monblog", None))

    def test_archives_widget_keeps_key_outside_with_pages(self):
        """Le widget Archives ne doit pas lire .Key après {{ with .Pages }} (crash hugo)."""
        sb = h._sidebar_partial("monblog", {"sidebar-1": [{"type": "archives", "title": "Archives"}]})
        self.assertIn("$key := .Key", sb)
        self.assertIn("{{ $key }}", sb)
        self.assertNotIn("<li><a href='{{ range first 1 . }}{{ .RelPermalink }}{{ end }}'>{{ .Key }}", sb)


class TestBuildSite(unittest.TestCase):
    def _build(self):
        td = Path(tempfile.mkdtemp(prefix="hugo-ut."))
        self.addCleanup(shutil.rmtree, td, ignore_errors=True)
        extracted = fixture_extracted(td)
        out = td / "site"
        summary = h.build_site(extracted, "monblog", out, base_url="http://localhost:8092")
        return extracted, out, summary

    def test_site_layout(self):
        extracted, out, summary = self._build()
        self.assertEqual(summary["posts"], 2)
        self.assertEqual(summary["pages"], 1)
        self.assertEqual(summary["assets_local"], 2)  # pic.jpg + paper.pdf
        self.assertEqual(summary["theme"], "montheme")
        self.assertEqual(summary["profile"], "montheme")
        self.assertTrue(summary["sidebar"])
        self.assertTrue((out / "content" / "_index.md").exists())
        self.assertEqual(len(list((out / "content" / "posts").glob("*.md"))), 2)
        self.assertEqual(len(list((out / "content" / "pages").glob("*.md"))), 1)

    def test_theme_nblogs_copied(self):
        _, out, _ = self._build()
        self.assertTrue((out / "themes" / "nblogs" / "theme.toml").exists())
        self.assertTrue((out / "themes" / "nblogs" / "layouts" / "_default" / "baseof.html").exists())

    def test_media_and_profile_css(self):
        _, out, _ = self._build()
        self.assertTrue((out / "static" / "images" / "2026" / "01" / "pic.jpg").exists())
        self.assertTrue((out / "static" / "css" / "profiles" / "montheme" / "style.css").exists())
        self.assertTrue((out / "static" / "css" / "profiles" / "montheme" / "images" / "logo.png").exists())

    def test_fidelity_sidebar_and_menu(self):
        _, out, _ = self._build()
        sb = out / "layouts" / "partials" / "sidebars" / "monblog.html"
        self.assertTrue(sb.exists())
        cfg = (out / "config.toml").read_text()
        self.assertIn('name = "Accueil"', cfg)
        self.assertIn('name = "Contact"', cfg)
        self.assertIn('slug = "monblog"', cfg)
        self.assertIn('profile = "montheme"', cfg)

    def test_title_apostrophes_in_front_matter(self):
        _, out, _ = self._build()
        joined = "\n".join(p.read_text() for p in (out / "content" / "posts").glob("*.md"))
        self.assertIn('title: "p\'tits fils d\'agitation"', joined)
        cfg = (out / "config.toml").read_text()
        self.assertIn("title = \"p'tits fils — Mon Blog\"", cfg)

    def test_custom_css_written(self):
        _, out, _ = self._build()
        css = (out / "static" / "css" / "blog-custom.css").read_text()
        self.assertIn(".post { margin: 0; }", css)


class TestBuildSiteFromRealZipFixture(unittest.TestCase):
    """Chaîne complète : backup e2e (serveur local) → ZIP → build_site → build hugo."""

    def test_zip_to_hugo(self):
        import argparse
        import backup.wizard as wz
        from tests.test_e2e import _args, _exit
        from backup.__main__ import backup_one

        with _exit("full") as base, tempfile.TemporaryDirectory() as tmp:
            td = Path(tmp)
            out_dir = str(td / "backups")
            result = backup_one("fixture", _args(out_dir, base))
            self.assertNotIn("error", result, result)
            zip_path = Path(result["zip"])

            extracted = td / "x"
            with zipfile.ZipFile(zip_path) as zf:
                zf.extractall(extracted)
            self.assertTrue((extracted / "hugo" / "nblogs" / "theme.toml").exists(),
                            "le ZIP doit embarquer le thème hugo vendoré")
            self.assertTrue((extracted / "GUIDE-HUGO.md").exists(),
                            "le ZIP doit contenir le guide Hugo")
            meta = json.loads((extracted / "metadata.json").read_text())
            self.assertIs(meta.get("hugo_theme"), True,
                          "metadata.json doit signaler hugo_theme")

            site = td / "site"
            summary = h.build_site(extracted, "fixture", site, base_url="http://localhost:8092")
            self.assertEqual(summary["posts"], 3)
            self.assertEqual(summary["pages"], 1)
            self.assertEqual(summary["theme"], "fixturetheme")
            self.assertTrue(summary["sidebar"])
            # pic.jpg et paper.pdf sont dans les articles → 2 médias réécrits localement
            self.assertGreaterEqual(summary["assets_local"], 2)
            # sygme sidebar produite
            self.assertTrue((site / "layouts" / "partials" / "sidebars" / "fixture.html").exists())
            # image réécrite dans le contenu des articles
            joined = "\n".join(p.read_text() for p in (site / "content" / "posts").glob("*.md"))
            self.assertIn("/images/2026/01/pic.jpg", joined)

    def test_real_hugo_build(self):
        """Ne s'exécute que si un binaire hugo est en cache (backups/hugo/bin)."""
        from backup.wizard import HUGO_BIN_DIR
        hugo_bin = HUGO_BIN_DIR / "hugo"
        if not hugo_bin.exists():
            self.skipTest("binaire hugo non présent (backups/hugo/bin/hugo)")

        with tempfile.TemporaryDirectory() as tmp:
            td = Path(tmp)
            extracted = fixture_extracted(td)
            site = td / "site"
            h.build_site(extracted, "monblog", site, base_url="http://localhost:8092")
            ok = h.run_hugo_build(site, str(hugo_bin))
            self.assertTrue(ok, "build hugo réel")
            index = site / "public" / "index.html"
            self.assertTrue(index.exists())
            html = index.read_text()
            self.assertIn("<title>p", html)
            self.assertIn("/css/profiles/montheme/style.css", html)
            posts = list((site / "public" / "post").rglob("index.html"))
            self.assertGreaterEqual(len(posts), 2)
            # une seule vraie page de post (l'autre étant l'alias) ; peu importe,
            # on vérifie que le contenu embarqué contient l'image locale
            joined = " ".join(p.read_text() for p in posts)
            self.assertIn("/images/2026/01/pic.jpg", joined)

    def test_minimalism_profile_renders_wordpress_dom(self):
        """Le profil minimalism doit reproduire le DOM attendu par minimalism/style.css."""
        from backup.wizard import HUGO_BIN_DIR
        hugo_bin = HUGO_BIN_DIR / "hugo"
        if not hugo_bin.exists():
            self.skipTest("binaire hugo non présent (backups/hugo/bin/hugo)")

        with tempfile.TemporaryDirectory() as tmp:
            td = Path(tmp)
            extracted = fixture_extracted(td, profile="minimalism")
            site = td / "site"
            h.build_site(extracted, "monblog", site, base_url="http://localhost:8092")
            ok = h.run_hugo_build(site, str(hugo_bin))
            self.assertTrue(ok, "build hugo réel (profil minimalism)")

            home = (site / "public" / "index.html").read_text()
            for sel in ('id="page"', 'id="header"', 'id="headerimg"', 'id="content" class="narrowcolumn"',
                        'id="sidebar"', 'id="searchform"', 'id="searchsubmit"', 'id="footer"',
                        'class="postmetadata"', 'class="entry"'):
                self.assertIn(sel, home, sel)
            self.assertGreaterEqual(home.count('class="post"'), 2, "posts en boucle sur l'accueil")
            # `profile` minimalism servi -> la vraie CSS du thème WP du ZIP
            self.assertIn("/css/profiles/minimalism/style.css", home)

            single = sorted((site / "public" / "post").rglob("index.html"))
            body = " ".join(p.read_text() for p in single)
            self.assertIn('class="postmetadata alt"', body)
            self.assertIn('<p class="nocomments">Comments are closed.</p>', body)


if __name__ == "__main__":
    unittest.main()