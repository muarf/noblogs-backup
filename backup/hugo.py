"""hugo.py — Republication d'une sauvegarde NoBlogs en site Hugo statique.

Transforme une archive décompressée (``wordpress-export.xml``, ``uploads/``,
``fidelity.json``, ``fidelity_media/``, ``theme/<theme>/``) en dépôt Hugo :

* ``content/posts/``, ``content/pages/`` — Markdown + front matter YAML
  (le HTML WordPress est conservé tel quel dans le corps, fidélité visuelle) ;
* ``static/images/`` — les médias du blog ;
* ``themes/nblogs/`` — le thème nblogs réduit, vendoré dans l'outil
  (ZIP ``hugo/nblogs/`` sinon ``backup/hugo/nblogs/``) ;
* ``config.toml`` — titre/tagline/profile, permaliens auto-détectés, menu ;
* fidélité appliquée depuis le ZIP : vraie CSS du thème, custom CSS, logo,
  bannière, fond, sidebar.

Le tout est 100 % hors-ligne : rien n'est téléchargé ni rsyncé. Seul le binaire
``hugo`` peut manquer au moment du build → téléchargement proposé (» comme on
lance Docker ») sinon fallback Docker.

Usage (fonctions, pas de CLI) :
    from backup.hugo import build_site, find_hugo, run_hugo_build
"""
from __future__ import annotations

import email.utils
import html as html_module
import json
import os
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
import unicodedata
import urllib.request
import xml.etree.ElementTree as ET
from pathlib import Path

# ------------------------------------------------------------------ constantes

HUGO_VERSION = "0.165.0"
_HUGO_RELEASES = "https://github.com/gohugoio/hugo/releases/download"

NS = {
    "content": "http://purl.org/rss/1.0/modules/content/",
    "excerpt": "http://wordpress.org/export/1.2/excerpt/",
    "wp": "http://wordpress.org/export/1.2/",
    "dc": "http://purl.org/dc/elements/1.1/",
}

URL_ATTR = re.compile(r'\s(?:src|href|srcset|data-src|poster)="([^"]*)"')
URL_RE = re.compile(r"https?://[^\s\"'<>]+")
SLUG_RE = re.compile(r"[^a-z0-9_-]+")

# URLs médias WordPress / NoBlogs à ré-écrire vers /images/...
_MEDIA_URL = re.compile(r"/(?:files|uploads|wp-content/uploads)/(.*)$")
_PARAM = re.compile(r"^\s*([a-zA-Z0-9_-]+)\s*=")

_HUGO_DIR = Path(__file__).resolve().parent / "hugo"
_VENDORED_THEME = _HUGO_DIR / "nblogs"


def _q(s: str) -> str:
    """Quote une chaîne pour du YAML / TOML (double-guillemets sûrs)."""
    s = (s or "").replace("\\", "\\\\").replace('"', '\\"')
    s = s.replace("\r", " ").replace("\n", " ").replace("\t", " ")
    return '"' + s + '"'


def _json_q(s: str) -> str:
    return json.dumps(s or "", ensure_ascii=False)


def _slugify(title: str) -> str:
    norm = unicodedata.normalize("NFKD", title).encode("ascii", "ignore").decode("utf-8")
    return SLUG_RE.sub("-", norm.lower()).strip("-")


# ---------------------------------------------------------------- parse WXR

def _text(el, tag):
    if el is None:
        return ""
    e = el.find(_ns_tag(tag))
    return (e.text or "").strip() if e is not None and e.text else ""


def _cdata(el, tag):
    return _text(el, tag)


def parse_date(s: str) -> str:
    s = (s or "").strip()
    if not s:
        return ""
    try:
        dt = email.utils.parsedate_to_datetime(s)
        return dt.astimezone(__import__("datetime").timezone.utc).isoformat()
    except Exception:
        try:
            s2 = s.strip().replace("Z", "+00:00").replace(" ", "T")
            return __import__("datetime").datetime.fromisoformat(s2).isoformat()
        except Exception:
            return ""


def _ns_tag(name: str) -> str:
    ns_alias = {"wp": NS["wp"], "content": NS["content"], "excerpt": NS["excerpt"], "dc": NS["dc"]}
    if ":" in name:
        prefix, local = name.split(":", 1)
        if prefix in ns_alias:
            return f"{{{ns_alias[prefix]}}}{local}"
    return name


def item_date(item) -> str:
    for raw in (_text(item, "wp:post_date"), _text(item, "pubDate")):
        d = parse_date(raw)
        if d:
            return d
    return "1970-01-01T00:00:00+00:00"


def parse_wxr(raw: bytes):
    try:
        return ET.fromstring(raw)
    except Exception:
        cleaned = re.sub(r"&([a-zA-Z]+);", r"\1", raw.decode("utf-8", "ignore"))
        return ET.fromstring(cleaned.encode("utf-8"))


def detect_permalink(links) -> str:
    for link in links:
        if re.search(r"/post/(\d{4})/\d{2}/\d{2}/", link):
            return "post/:year/:month/:day/:slug/"
        if re.search(r"/archives/(\d{4})/\d{2}/\d{2}/", link):
            return "archives/:year/:month/:day/:slug/"
        if re.search(r"/(\d{4})/\d{2}/\d{2}/", link):
            return ":year/:month/:day/:slug/"
        if "/archives/" in link:
            return "archives/:slug/"
        if "/post/" in link:
            return "post/:slug/"
    return "posts/:slug/"


# ---------------------------------------------------------------- front matter

def clean_html_for_markdown(html_text: str) -> str:
    """Évite que Goldmark n'interprète les lignes indentées comme du code pré."""
    if not html_text:
        return ""
    out = []
    in_pre = False
    for line in html_text.splitlines():
        if "<pre" in line:
            in_pre = True
        if "</pre>" in line:
            in_pre = False
            out.append(line)
            continue
        out.append(line if in_pre else line.lstrip(" \t"))
    return "\n".join(out)


def yaml_front(title, date, slug, categories, tags, author, description, draft, aliases=None):
    clean_title = html_module.unescape(html_module.unescape(title or ""))
    clean_desc = html_module.unescape(html_module.unescape(description or ""))
    lines = ["---", f"title: {_q(clean_title)}", f"date: {_q(date)}", f"slug: {_q(slug)}"]
    if author:
        lines.append(f"author: {_q(author)}")
    if clean_desc:
        lines.append(f"description: {_q(clean_desc)}")
    if categories:
        lines.append("categories:")
        lines.extend(f"  - {_q(c)}" for c in categories)
    if tags:
        lines.append("tags:")
        lines.extend(f"  - {_q(t)}" for t in tags)
    lines.append(f"draft: {str(bool(draft)).lower()}")
    if aliases:
        lines.append("aliases:")
        lines.extend(f"  - {_q(a)}" for a in aliases)
    lines.append("---")
    return "\n".join(lines)


def render_front(item: dict) -> str:
    aliases = []
    link = item.get("link") or ""
    slug = item.get("slug") or ""

    if link:
        rel = re.sub(r"^https?://[^/]+", "", link).strip()
        if rel and rel != "/":
            aliases.append(rel)
            aliases.append(rel.rstrip("/") if rel.endswith("/") else rel + "/")
            parts = [p for p in rel.split("/") if p]
            if parts:
                url_slug = parts[-1]
                if url_slug != slug:
                    aliases.append(f"/post/{url_slug}/")
                    m_date = re.search(r"/(\d{4}/\d{2}/\d{2})/", rel)
                    if m_date:
                        ymd = m_date.group(1)
                        aliases.append(f"/post/{ymd}/{slug}/")
                        aliases.append(f"/post/{ymd}/{url_slug}/")

    if slug:
        short_post = f"/post/{slug}/"
        if short_post not in aliases:
            aliases.append(short_post)

    seen, dedup = set(), []
    for a in aliases:
        if a and a not in seen:
            seen.add(a)
            dedup.append(a)

    return yaml_front(
        item["title"], item["date"], item.get("slug") or item.get("id") or "post",
        item.get("categories") or [], item.get("tags") or [], item.get("author") or "",
        item.get("excerpt") or "", item.get("status") == "draft", aliases=dedup,
    )


# --------------------------------------------------------------- AssetMapper

class AssetMapper:
    """Ré-écrit les URLs médias source vers /images/... si le fichier existe."""

    def __init__(self, assets_dir: Path | None):
        self.dir = assets_dir
        self.existing: dict[str, str] = {}
        self.total = 0

    def _infer_rel(self, url: str) -> str:
        m = _MEDIA_URL.search(url.split("?")[0].split("#")[0])
        if m:
            return m.group(1).lstrip("/")
        return ""

    def rewrite(self, html: str) -> str:
        if not html:
            return html
        out_parts, last = [], 0
        for m in URL_ATTR.finditer(html):
            start, end = m.start(), m.end()
            out_parts.append(html[last:start])
            val = m.group(1)
            if self.dir is not None and (
                "/files/" in val or "/wp-content/uploads/" in val or "/uploads/" in val
            ):
                nval = URL_RE.sub(lambda u: self._local(u.group(0)) or u.group(0), val)
                prefix = html[start : start + m.group(0).find(val)]
                out_parts.append(prefix + nval + '"')
            else:
                out_parts.append(html[start:end])
            last = end
        out_parts.append(html[last:])
        return "".join(out_parts)

    def _local(self, url: str) -> str:
        url = url.split("?")[0].split("#")[0]
        if url in self.existing:
            return self.existing[url]
        rel = self._infer_rel(url)
        if not rel:
            return ""
        local = self.dir / rel if self.dir else None
        if local is not None and local.exists() and local.stat().st_size > 0:
            mapped = "/images/" + rel
            self.existing[url] = mapped
            self.total += 1
            return mapped
        return ""


# ------------------------------------------------------------------ fidélité

def _theme_from_zip(extracted: Path) -> tuple[str, Path | None]:
    """Retourne (nom_du_thème, dossier_theme) depuis le ZIP (si présent)."""
    theme_dir = extracted / "theme"
    fidelity_file = extracted / "fidelity.json"
    theme_name = ""
    if theme_dir.is_dir():
        entries = [d for d in theme_dir.iterdir() if d.is_dir() and any(d.iterdir())]
        if len(entries) == 1:
            theme_name = entries[0].name
    if fidelity_file.exists():
        try:
            fid = json.loads(fidelity_file.read_text(encoding="utf-8"))
            if fid.get("theme") and (theme_dir / str(fid["theme"])).is_dir():
                theme_name = str(fid["theme"])
        except Exception:
            pass
    folder = theme_dir / theme_name if (theme_name and (theme_dir / theme_name).is_dir()) else None
    return theme_name, folder


def _rewrite_css_urls(css: str, assets: Path | None, mapper: AssetMapper) -> str:
    """Ré-écrit les URLs uploads/ du CSS vers /images/ (si le fichier existe)."""
    if not css:
        return ""
    def repl(m):
        url = m.group(1)
        local = mapper._local(url)
        if local:
            return f"url({local})"
        return m.group(0)
    out = re.sub(r"url\((['\"]?)([^)'\"]+)\1\)", repl, css)
    out = re.sub(r"image-set\(\s*url\(([^)]*)\)", repl, out)
    return out


def _injected_widget_css(fidelity: dict, extracted: Path, site: Path) -> str:
    """CSS d'habillage nblogs générée à partir du logo / bannière / fond réels."""
    rules = []
    media = extracted / "fidelity_media"
    profile = fidelity.get("theme") or ""
    masthead = "#branding" if profile == "twentyten" else "#masthead"

    logo = sorted(media.glob("logo.*")) if media.is_dir() else []
    if logo:
        src = logo[0]
        dest = site / "static" / "css" / ("logo" + src.suffix)
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dest)
        rules.append(
            f"{masthead} .site-title{{"
            f"background:url('/css/logo{src.suffix}') no-repeat left center;"
            f"background-size:contain;min-height:32px;padding-left:40px;}}"
        )

    header = sorted(media.glob("header-image.*")) if media.is_dir() else []
    if header:
        src = header[0]
        dest = site / "static" / "css" / ("blog-header" + src.suffix)
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dest)
        rules.append(
            f"{masthead}{{background:url('/css/blog-header{src.suffix}') no-repeat center/cover;}}"
        )

    bg = fidelity.get("background") or {}
    bg_local = bg.get("background_image_local")
    if bg_local:
        src = media / bg_local if media.is_dir() else None
        if src and src.exists():
            ext = src.suffix
            dest = site / "static" / "css" / ("background" + ext)
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dest)
            rules.append(f"body{{background-image:url('/css/background{ext}');}}")
        rules.append(
            f"body{{"
            + (f"background-repeat:{bg.get('repeat','repeat')};" if bg.get("repeat") else "")
            + "}"
        )
    elif bg.get("background_color") and not bg.get("background_image"):
        rules.append(f"body{{background-color:#{bg['background_color']};}}")

    link_color = fidelity.get("link_color")
    if link_color:
        rules.append(f".entry-content a{{color:{link_color};}}")

    if fidelity.get("theme_colors", {}).get("header_background_color"):
        rules.append(f"{masthead}{{background-color:{fidelity['theme_colors']['header_background_color']};}}")

    return "\n".join(rules)


def _apply_fidelity(extracted: Path, site: Path, fidelity: dict, mapper: AssetMapper) -> dict:
    """Applique la fidélité du ZIP au dépôt Hugo. Retourne le résumé."""
    theme_name, theme_folder = _theme_from_zip(extracted)
    if theme_name and theme_folder:
        profile_dst = site / "static" / "css" / "profiles" / theme_name
        if profile_dst.exists():
            shutil.rmtree(profile_dst, ignore_errors=True)
        shutil.copytree(theme_folder, profile_dst, dirs_exist_ok=True)

    custom_css = fidelity.get("custom_css") or ""
    css_parts = []
    if custom_css:
        css_parts.append(_rewrite_css_urls(custom_css, extracted / "uploads", mapper))
    injected = _injected_widget_css(fidelity, extracted, site)
    if injected:
        css_parts = [injected] + css_parts
    css = "\n".join(p for p in css_parts if p)

    extras = False
    if css:
        dest = site / "static" / "css" / "blog-custom.css"
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(css, encoding="utf-8")
        extras = True

    return {"theme": theme_name, "profile_css": bool(theme_name and theme_folder), "extras": extras}


# ------------------------------------------------------------------ config

def menu_lines(menu_items) -> str:
    if not menu_items:
        return ""
    out = []
    weight = 0
    seen: set[str] = set()
    for item in menu_items:
        if not item:
            continue
        name = (item.get("title") or "").strip()
        href = (item.get("href") or "").strip()
        if not name or not href:
            continue
        weight += 1
        out.append("[[menu.main]]")
        out.append(f"  name = {_q(name)}")
        out.append(f"  url = {_q(href)}")
        out.append(f"  weight = {weight}")
        out.append("")
        for child in item.get("children") or []:
            cname = (child.get("title") or "").strip()
            chref = (child.get("href") or "").strip()
            if not cname or not chref:
                continue
            weight += 1
            out.append("[[menu.main]]")
            out.append(f"  name = {_q(cname)}")
            out.append(f"  url = {_q(chref)}")
            out.append(f"  weight = {weight}")
            out.append("")
    return "\n".join(out)


def _sidebar_partial(slug: str, sidebars: dict) -> str | None:
    """Génère le partial Hugo ``sidebars/<slug>.html`` depuis fidelity sidebars."""
    widgets = []
    for sb_id in sorted(sidebars or {}):
        widgets += sidebars.get(sb_id) or []
    if not widgets:
        return None

    blocks: list[str] = []
    for i, w in enumerate(widgets):
        if not isinstance(w, dict):
            continue
        wtype = w.get("type") or "custom_html"
        title = html_module.escape((w.get("title") or "").strip())
        title_html = f"<h3 class='widget-title'>{title}</h3>" if title else ""

        if wtype == "search":
            body = ("<form role='search' method='get' class='search-form' action=''>"
                    "<input type='search' class='search-field' placeholder='Rechercher…' name='s'>"
                    "<button type='submit' class='search-submit'>Rechercher</button></form>")
        elif wtype == "recent-posts":
            body = ("{{- range first 6 (where $.Site.RegularPages \"Type\" \"in\" (slice \"post\" \"posts\")).ByDate.Reverse }}"
                    "<li><a href='{{ .RelPermalink }}'>{{ .Title }}</a></li>\n"
                    "{{- end }}")
            body = "<ul class='widget-list'>\n" + body + "\n</ul>"
        elif wtype == "categories":
            body = ("{{- range (.Site.GetPage \"/categories\").Pages }}{{ if .Pages }}"
                    "<li><a href='{{ .RelPermalink }}'>{{ .LinkTitle }}</a></li>\n"
                    "{{- end }}{{ end }}")
            body = "<ul class='widget-list'>\n" + body + "\n</ul>"
        elif wtype == "archives":
            body = ("{{- range first 12 ($.Site.RegularPages.GroupByDate \"2006-01\") }}{{ with .Pages }}"
                    "<li><a href='{{ range first 1 . }}{{ .RelPermalink }}{{ end }}'>{{ .Key }}</a></li>\n"
                    "{{- end }}{{ end }}")
            body = "<ul class='widget-list'>\n" + body + "\n</ul>"
        elif wtype == "media_video":
            url = html_module.escape(w.get("url") or "")
            body = f"<iframe src='{url}' allowfullscreen></iframe>"
        else:
            content = (w.get("content") or "").strip()
            body = re.sub(r"{{|}}", lambda m: '{{"%s"}}' % m.group(0), content)

        blocks.append(
            f"<aside class='widget widget-{i}'>\n{title_html}\n{body}\n</aside>"
        )
    return "\n".join(blocks)


def make_config(
    slug: str,
    title: str,
    tagline: str,
    lang: str,
    links: list[str],
    base_url: str,
    profile: str,
    extras: bool,
    menu_items,
    sidebar_widgets: list,
    permalink_post: str = "",
    permalink_page: str = "",
) -> str:
    lang = (lang or "fr-fr").lower()
    p_posts = permalink_post or detect_permalink(links)
    p_pages = permalink_page or ":slug/"
    title = html_module.unescape(html_module.unescape(title or slug))
    tagline = html_module.unescape(html_module.unescape(tagline or ""))

    params = []
    params.append(f"  slug = {_q(slug)}")
    if profile:
        params.append(f"  profile = {_q(profile)}")
    if tagline:
        params.append(f"  tagline = {_q(tagline)}")
        params.append(f"  description = {_q(tagline)}")
    if sidebar_widgets:
        params.append(f"  sidebar = {_json_q(sidebar_widgets)}")
    if extras:
        params.append('  themeExtras = ["/css/blog-custom.css"]')

    cfg = f"""# Généré par noblogs-backup — ne pas éditer à la main
baseURL = {_q((base_url or "/").rstrip('/') + '/')}
languageCode = {_q(lang)}
defaultContentLanguage = {_q(lang)}
title = {_q(title)}
theme = "nblogs"
enableRobotsTXT = true

[markup.goldmark.renderer]
unsafe = true

[taxonomies]
  category = 'categories'
  tag = 'tags'

[params]
{chr(10).join(params)}

[permalinks]
  posts = {_q(p_posts)}
  pages = {_q(p_pages)}
"""
    return cfg + menu_lines(menu_items)


# ------------------------------------------------------------------ theme

def copy_theme(src: Path, site: Path) -> None:
    dst = site / "themes" / "nblogs"
    if dst.exists():
        shutil.rmtree(dst, ignore_errors=True)
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(src, dst)


def _theme_source(extracted: Path) -> Path:
    """Thème nblogs : version de l'outil en priorité, sinon celui du ZIP (fallback).

    L'outil est mis à jour avec son thème (le ZIP peut embarquer une copie
    antérieure du thème) ; on préfère donc la copie locale à jour.
    """
    zipped = extracted / "hugo" / "nblogs"
    if _VENDORED_THEME.exists() and any(_VENDORED_THEME.rglob("*.html")):
        return _VENDORED_THEME
    if zipped.is_dir() and any(zipped.rglob("*.html")):
        return zipped
    return _VENDORED_THEME


# ------------------------------------------------------------------ build_site

def build_site(extracted: Path, slug: str, out_dir: Path, base_url: str = "") -> dict:
    """Construit le dépôt Hugo dans ``out_dir`` depuis l'archive décompressée.

    Tout est local au ZIP (aucune dépendance réseau). Retourne un résumé.
    """
    wxr = extracted / "wordpress-export.xml"
    if not wxr.exists():
        return {"error": f"wordpress-export.xml introuvable dans {extracted}"}

    root = parse_wxr(wxr.read_bytes())
    channel = root.find("channel")
    ch_title = _text(channel, "title")
    ch_desc = _text(channel, "description")
    ch_lang = _text(channel, "language")

    items: list[dict] = []
    links: list[str] = []
    for item in channel.iter("item") if channel is not None else []:
        post_type = _text(item, "wp:post_type")
        if post_type not in ("post", "page"):
            continue
        link = _text(item, "link")
        links.append(link)
        cats = [c.text for c in item.findall("category") if c.get("domain") == "category" and c.text]
        tags = [c.text for c in item.findall("category") if c.get("domain") == "post_tag" and c.text]
        items.append(
            {
                "post_type": post_type,
                "id": _text(item, "wp:post_id"),
                "slug": (_text(item, "wp:post_name") or _text(item, "wp:post_id"))[:120],
                "title": _text(item, "title"),
                "link": link,
                "date": item_date(item),
                "status": _text(item, "wp:status") or "publish",
                "author": _text(item, "dc:creator"),
                "excerpt": _cdata(item, "excerpt:encoded"),
                "content": _cdata(item, "content:encoded") or "",
                "categories": cats,
                "tags": tags,
            }
        )

    # Fidélité depuis le ZIP
    fidelity: dict = {}
    fid_file = extracted / "fidelity.json"
    if fid_file.exists():
        try:
            fidelity = json.loads(fid_file.read_text(encoding="utf-8"))
        except Exception:
            fidelity = {}

    assets_dir = extracted / "uploads"
    mapper = AssetMapper(assets_dir if assets_dir.is_dir() else None)

    out_dir.mkdir(parents=True, exist_ok=True)
    if out_dir.exists():
        for entry in out_dir.iterdir():
            if entry.is_dir():
                shutil.rmtree(entry, ignore_errors=True)
            else:
                entry.unlink(missing_ok=True)

    posts_dir = out_dir / "content" / "posts"
    pages_dir = out_dir / "content" / "pages"
    posts_dir.mkdir(parents=True)
    pages_dir.mkdir(parents=True)

    used_names: set[str] = set()
    n_post = n_page = 0

    for it in items:
        body = mapper.rewrite(it["content"])
        body = clean_html_for_markdown(body)
        fm = render_front(it)
        base_name = _slugify(it.get("slug") or it.get("title") or it.get("id") or "post")[:180] or f"post-{n_post}"
        candidate = base_name
        idx = 1
        while candidate in used_names:
            candidate = f"{base_name}-{idx}"
            idx += 1
        used_names.add(candidate)
        if it["post_type"] == "post":
            n_post += 1
            p = posts_dir / f"{candidate}.md"
        else:
            n_page += 1
            p = pages_dir / f"{candidate}.md"
        p.write_text(fm + "\n\n" + body + "\n", encoding="utf-8")

    (out_dir / "content" / "_index.md").write_text("---\n---\n", encoding="utf-8")

    # Thème nblogs (ZIP en priorité)
    copy_theme(_theme_source(extracted), out_dir)

    # Médias → static/images
    if mapper.dir is not None:
        images = out_dir / "static" / "images"
        images.mkdir(parents=True, exist_ok=True)
        shutil.copytree(mapper.dir, images, dirs_exist_ok=True, ignore_dangling_symlinks=True)

    # Fidélité : thème réel (CSS), custom CSS, logo/bannière/fond, sidebar
    theme_name, _folder = _theme_from_zip(extracted)
    fid_summary = _apply_fidelity(extracted, out_dir, fidelity, mapper)
    profile = fidelity.get("theme") or theme_name or ""

    sidebar_partial = _sidebar_partial(slug, fidelity.get("sidebars") or {})
    if sidebar_partial:
        sb_dir = out_dir / "layouts" / "partials" / "sidebars"
        sb_dir.mkdir(parents=True, exist_ok=True)
        (sb_dir / f"{slug}.html").write_text(sidebar_partial, encoding="utf-8")

    # Menu depuis fidelity.json
    menu_items = fidelity.get("menu_items") or []

    (out_dir / "config.toml").write_text(
        make_config(
            slug=slug,
            title=fidelity.get("title") or ch_title or slug,
            tagline=fidelity.get("tagline") or ch_desc,
            lang=ch_lang,
            links=links,
            base_url=base_url,
            profile=profile,
            extras=fid_summary["extras"],
            menu_items=menu_items,
            sidebar_widgets=[],  # sidebar gérée par le partial sidebars/<slug>
        ),
        encoding="utf-8",
    )

    return {
        "slug": slug,
        "title": fidelity.get("title") or ch_title or slug,
        "posts": n_post,
        "pages": n_page,
        "permalink": detect_permalink(links),
        "assets_local": mapper.total,
        "theme": theme_name,
        "profile": profile,
        "sidebar": bool(sidebar_partial),
        "out": str(out_dir),
    }


# ------------------------------------------------------------------ build hugo

def find_hugo() -> str | None:
    """Chemin du binaire hugo (PATH le cas échéant), sinon None."""
    return shutil.which("hugo")


def _hugo_target() -> tuple[str, str]:
    """(plateforme, arch) pour les assets de release GitHub (informe le download)."""
    plat = {"linux": "linux", "darwin": "darwin", "win32": "windows"}.get(sys.platform, "linux")
    machine = os.uname().machine if hasattr(os, "uname") else "x86_64"
    arch = {"x86_64": "amd64", "aarch64": "arm64", "arm64": "arm64", "i686": "386"}.get(machine, "amd64")
    return plat, arch


def download_hugo(dest_dir: Path, version: str = HUGO_VERSION) -> str | None:
    """Télécharge le binaire hugo extended et retourne son chemin (None si échec).

    Assets: ``hugo_extended_<version>_<linux|darwin>-<amd64|arm64|universal>.tar.gz``
    """
    plat, arch = _hugo_target()
    if plat == "darwin":
        arch = "universal"  # macOS : asset universel (intel + arm)
    asset = f"hugo_extended_{version}_{plat}-{arch}.tar.gz"
    url = f"{_HUGO_RELEASES}/v{version}/{asset}"
    print(f"\n  Téléchargement de hugo {version} : {url}")
    dest_dir.mkdir(parents=True, exist_ok=True)
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "noblogs-backup"})
        with urllib.request.urlopen(req, timeout=120) as resp, tempfile.TemporaryFile() as tmp:
            shutil.copyfileobj(resp, tmp)
            tmp.seek(0)
            with tarfile.open(fileobj=tmp, mode="r:gz") as tar:
                try:
                    tar.extractall(dest_dir, filter="data")
                except TypeError:
                    tar.extractall(dest_dir)
    except Exception as e:
        print(f"  ✗ Échec du téléchargement : {e}")
        return None
    hugo = dest_dir / "hugo"
    if plat == "windows":
        hugo = dest_dir / "hugo.exe"
    if not hugo.exists():
        return None
    hugo.chmod(0o755)
    print(f"  ✓ hugo prêt : {hugo}")
    return str(hugo)


def run_hugo_build(site: Path, hugo_bin: str | None = None) -> bool:
    """Lance le build Hugo du dépôt. Retourne True si le build a produit public/."""
    cmd = [hugo_bin or "hugo", "--source", str(site), "--logLevel", "warn", "--quiet"]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True)
    except FileNotFoundError:
        print("  ✗ binaire hugo introuvable.")
        return False
    if proc.returncode != 0:
        print((proc.stderr or proc.stdout).strip() or f"  ✗ build hugo échoué (code {proc.returncode}).")
        return False
    return (site / "public").exists() and any((site / "public").iterdir())


def hugo_docker_build(site: Path, version: str = HUGO_VERSION) -> bool:
    """Fallback Docker : hugo dans un conteneur (binaire non embarqué)."""
    image = f"klakegg/hugo:{version}-ext-alpine"
    cmd = ["docker", "run", "--rm", "-v", f"{site}:/site", "-w", "/site", image]
    try:
        subprocess.run(cmd, capture_output=True, text=True)
    except FileNotFoundError:
        print("  ✗ docker introuvable.")
        return False
    return (site / "public").exists() and any((site / "public").iterdir())