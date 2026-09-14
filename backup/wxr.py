"""wxr.py — Génération d'un export WordPress WXR 1.2 (XML).

Convertit les articles, pages et métadonnées récolés par `scraper.py` en un
fichier `rss` compatible avec l'outil *Outils > Importer > WordPress* de toute
installation WordPress, ainsi qu'avec `wp import` (WP-CLI).
"""
from __future__ import annotations

import html
import os
import re
import unicodedata
from datetime import datetime, timezone
from pathlib import Path

from .html_clean import clean_content

_DEFAULT_DATE = "2022-01-01 12:00:00"

_BASE_ITEM = """  <item>
    <title><![CDATA[{title}]]></title>
    <link>{link}</link>
    <dc:creator><![CDATA[{creator}]]></dc:creator>
    <guid isPermaLink="false">{guid}</guid>
    <description></description>
    <content:encoded><![CDATA[{content}]]></content:encoded>
    <wp:post_id>{pid}</wp:post_id>
    <wp:post_date><![CDATA[{date}]]></wp:post_date>
    <wp:post_date_gmt><![CDATA[{gmt}]]></wp:post_date_gmt>
    <wp:comment_status>{comment_status}</wp:comment_status>
    <wp:ping_status>{ping_status}</wp:ping_status>
    <wp:post_name><![CDATA[{name}]]></wp:post_name>
    <wp:status><![CDATA[{status}]]></wp:status>
    <wp:post_parent>0</wp:post_parent>
    <wp:menu_order>0</wp:menu_order>
    <wp:post_type><![CDATA[{post_type}]]></wp:post_type>
{extra}  </item>"""


def _attachment_item(
    pid: int,
    slug: str,
    clean_site_url: str,
    attachment: dict,
    dt_str: str,
    gmt_str: str,
) -> str:
    url = attachment.get("url", "")
    title = clean_content(attachment.get("title") or "attachment")
    name = _slugify(title) or f"attachment-{pid}"
    extra = f"    <wp:attachment_url>{url}</wp:attachment_url>\n"
    return _BASE_ITEM.format(
        title=title,
        link=url,
        creator=slug,
        guid=f"{clean_site_url}/?attachment_id={pid}",
        content="",
        pid=pid,
        date=dt_str,
        gmt=gmt_str,
        comment_status="open",
        ping_status="open",
        name=name,
        status="inherit",
        post_type="attachment",
        extra=extra,
    )


def _parse_datetime(raw: str) -> datetime | None:
    """Parse une date RSS (RFC-822) ou ISO 8601. Retourne un datetime aware ou None."""
    raw = (raw or "").strip()
    if not raw:
        return None
    try:
        return datetime.strptime(raw, "%a, %d %b %Y %H:%M:%S %z")
    except ValueError:
        pass
    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None


def _local_gmt(dt: datetime | None) -> tuple[str, str]:
    """(date_locale, date_gmt) au format « YYYY-MM-DD HH:MM:SS »."""
    if dt is None:
        return _DEFAULT_DATE, _DEFAULT_DATE
    local = dt.strftime("%Y-%m-%d %H:%M:%S")
    gmt = dt.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    return local, gmt


def rewrite_media_urls(content: str, slug: str, site_url: str) -> str:
    """Réécrit les URLs NoBlogs (/files/) vers le chemin WordPress standard."""
    if not content:
        return content
    base = site_url.rstrip("/")
    domains = rf"(?:{re.escape(slug)}\.noblogs\.org|noblogs\.org)"
    content = re.sub(rf"https?://{domains}/files/", f"{base}/wp-content/uploads/", content)
    content = re.sub(r"https?://[^/]+/files/", f"{base}/wp-content/uploads/", content)
    content = re.sub(r"(?<=[\"'=])/?files/", "wp-content/uploads/", content)
    return content


def _slugify(title: str) -> str:
    norm = unicodedata.normalize("NFKD", title).encode("ascii", "ignore").decode("utf-8")
    return re.sub(r"[^a-z0-9_-]+", "-", norm.lower()).strip("-")


def generate_wxr(
    slug: str,
    title: str,
    site_url: str,
    posts: list[dict],
    pages: list[dict],
    authors: dict[str, str] | None = None,
    language: str = "fr-FR",
    attachments: list[dict] | None = None,
) -> str:
    """Génére le contenu XML WXR 1.2 complet.

    * ``slug``        – identifiant court du blog (utilisé en login d'auteur).
    * ``site_url``    – URL cible de comparaison/remplacement (ex. l'ancien domaine).
    * ``authors``     – mapping login → email optionnel (défaut : admin@exemple.org).
    * ``attachments`` – liste de médias ``{url, title}`` → items ``<wp:attachment>``
      (l'importeur WordPress rattache alors les fichiers aux articles).
    """
    if authors is None:
        authors = {slug: f"{slug}@backup.noblogs.org"}

    clean_site_url = site_url.rstrip("/")

    xml: list[str] = []
    xml.append('<?xml version="1.0" encoding="UTF-8" ?>')
    xml.append('<rss version="2.0"')
    xml.append('  xmlns:excerpt="http://wordpress.org/export/1.2/excerpt/"')
    xml.append('  xmlns:content="http://purl.org/rss/1.0/modules/content/"')
    xml.append('  xmlns:wfw="http://wellformedweb.org/CommentAPI/"')
    xml.append('  xmlns:dc="http://purl.org/dc/elements/1.1/"')
    xml.append('  xmlns:wp="http://wordpress.org/export/1.2/">')
    xml.append("<channel>")
    xml.append(f"  <title><![CDATA[{clean_content(title)}]]></title>")
    xml.append(f"  <link>{clean_site_url}</link>")
    xml.append(f"  <language>{language}</language>")
    xml.append("  <wp:wxr_version>1.2</wp:wxr_version>")
    xml.append(f"  <wp:base_site_url>{clean_site_url}</wp:base_site_url>")
    xml.append(f"  <wp:base_blog_url>{clean_site_url}</wp:base_blog_url>")

    for login, email in authors.items():
        xml.append("  <wp:author>")
        xml.append("    <wp:author_id>1</wp:author_id>")
        xml.append(f"    <wp:author_login><![CDATA[{login}]]></wp:author_login>")
        xml.append(f"    <wp:author_email><![CDATA[{email}]]></wp:author_email>")
        xml.append(f"    <wp:author_display_name><![CDATA[{login}]]></wp:author_display_name>")
        xml.append("  </wp:author>")

    used_cats: set[str] = set()
    for p in posts:
        for c in p.get("cats", []):
            used_cats.add(c)
    for c in sorted(used_cats):
        c_slug = _slugify(c)
        xml.append("  <wp:category>")
        xml.append(f"    <wp:term_id>{len(xml) + 10}</wp:term_id>")
        xml.append(f"    <wp:category_nicename><![CDATA[{c_slug}]]></wp:category_nicename>")
        xml.append(f"    <wp:cat_name><![CDATA[{clean_content(c)}]]></wp:cat_name>")
        xml.append("  </wp:category>")

    post_id = 1000
    used_ids: set[int] = set()

    def reserve_id(preferred: int | None = None) -> int:
        """Attribue un id unique (préféré sinon séquentiel)."""
        nonlocal post_id
        if isinstance(preferred, int) and preferred not in used_ids:
            used_ids.add(preferred)
            return preferred
        while True:
            post_id += 1
            if post_id not in used_ids:
                used_ids.add(post_id)
                return post_id

    for p in posts:
        pid = reserve_id(p.get("id"))

        raw_date = p.get("date") or ""
        dt = _parse_datetime(raw_date)
        dt_str, gmt_str = _local_gmt(dt)

        orig_slug = ""
        link = p.get("link", "")
        if link and "/post/" in link:
            clean_link = link.strip().rstrip("/")
            if clean_link.split("/"):
                orig_slug = clean_link.split("/")[-1]
        p_slug = orig_slug or _slugify(p.get("title", "")) or f"post-{pid}"

        p_title = clean_content(p.get("title", ""))
        content = rewrite_media_urls(clean_content(p.get("content", "")), slug, clean_site_url)
        xml.append("  <item>")
        xml.append(f"    <title><![CDATA[{p_title}]]></title>")
        xml.append(f"    <link>{link}</link>")
        if raw_date:
            xml.append(f"    <pubDate>{raw_date}</pubDate>")
        xml.append(f"    <dc:creator><![CDATA[{p.get('author', slug)}]]></dc:creator>")
        xml.append(f'    <guid isPermaLink="false">{clean_site_url}/?p={pid}</guid>')
        xml.append("    <description></description>")
        xml.append(f"    <content:encoded><![CDATA[{content}]]></content:encoded>")
        xml.append(f"    <wp:post_id>{pid}</wp:post_id>")
        xml.append(f"    <wp:post_date><![CDATA[{dt_str}]]></wp:post_date>")
        xml.append(f"    <wp:post_date_gmt><![CDATA[{gmt_str}]]></wp:post_date_gmt>")
        xml.append("    <wp:status><![CDATA[publish]]></wp:status>")
        xml.append(f"    <wp:post_name><![CDATA[{p_slug}]]></wp:post_name>")
        xml.append("    <wp:post_type><![CDATA[post]]></wp:post_type>")
        for c in p.get("cats", []):
            c_slug = _slugify(c)
            xml.append(f'    <category domain="category" nicename="{c_slug}"><![CDATA[{clean_content(c)}]]></category>')
        xml.append("  </item>")

    for pg in pages:
        pid = reserve_id()
        pg_slug = pg.get("slug") or _slugify(pg.get("title", "")) or f"page-{pid}"
        pg_dt = _parse_datetime(pg.get("date") or "")
        pg_dt_str, pg_gmt_str = _local_gmt(pg_dt)
        xml.append("  <item>")
        xml.append(f"    <title><![CDATA[{clean_content(pg.get('title', ''))}]]></title>")
        xml.append(f"    <link>{pg.get('url', '')}</link>")
        xml.append(f"    <dc:creator><![CDATA[{slug}]]></dc:creator>")
        xml.append(f'    <guid isPermaLink="false">{clean_site_url}/?page_id={pid}</guid>')
        pg_content = rewrite_media_urls(clean_content(pg.get("content", "")), slug, clean_site_url)
        xml.append(f"    <content:encoded><![CDATA[{pg_content}]]></content:encoded>")
        xml.append(f"    <wp:post_id>{pid}</wp:post_id>")
        xml.append(f"    <wp:post_date><![CDATA[{pg_dt_str}]]></wp:post_date>")
        xml.append(f"    <wp:post_date_gmt><![CDATA[{pg_gmt_str}]]></wp:post_date_gmt>")
        xml.append("    <wp:status><![CDATA[publish]]></wp:status>")
        xml.append(f"    <wp:post_name><![CDATA[{pg_slug}]]></wp:post_name>")
        xml.append("    <wp:post_type><![CDATA[page]]></wp:post_type>")
        xml.append("  </item>")

    for att in attachments or []:
        pid = reserve_id()
        att_title = os.path.basename(att.get("path", att.get("url", ""))) or "attachment"
        xml.append(_attachment_item(pid, slug, clean_site_url, {**att, "title": att_title}, *_local_gmt(None)))

    xml.append("</channel>")
    xml.append("</rss>")
    return "\n".join(xml)


def write_wxr(path: Path, *args, **kwargs) -> Path:
    """Écrit l'export WXR dans ``path`` et renvoie``path``."""
    path.write_text(generate_wxr(*args, **kwargs), encoding="utf-8")
    return path