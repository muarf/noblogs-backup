"""fidelity.py — Extraction complète de la fidélité visuelle d'un blog NoBlogs.

Analyse le HTML live pour capturer :
- Thème, titre, tagline
- Custom CSS (Customizer, styles inline, couleurs)
- Image de bannière (header image) et logo personnalisé
- Image/couleur de fond
- Widgets de sidebar (types, contenu, ordre)
- Menu de navigation (titres, URLs, sous-menus)
- Couleurs spécifiques au thème (TwentyFifteen header/sidebar)

Retourne un dict sérialisable en JSON ("fidelity.json") qui sera inclus
dans le ZIP, et un dossier de médias fidélité (logo, header-image…).
"""
from __future__ import annotations

import json
import os
import re
from pathlib import Path

from bs4 import BeautifulSoup

from .http import fetch_url

# ---------------------------------------------------------------- constants

EXCLUDE_CSS_IDS = {
    "wp-block-library-inline-css",
    "global-styles-inline-css",
    "classic-theme-styles-inline-css",
    "wp-img-auto-sizes-contain-inline-css",
    "core-block-supports-inline-css",
}

KEEP_CSS_KEYWORDS = [
    "custom-css", "custom-background", "custom-header", "header-css",
    "header-styles", "inline-css", "style-inline", "footnotes", "color",
]

# Blocs Gutenberg « wp-block-*-inline-css » : CSS généré par le cœur WP,
# non spécifique au blog — inutile dans fidelity.json.
_BLOCK_INLINE_CSS = re.compile(r"^wp-block-[a-z0-9-]+-inline-css$")
_SOURCEURL_COMMENT = re.compile(r"(?m) *\/\*# ?sourceURL=[^*]*?\*\/")

# Dimensions de bannière par thème (source: Theme Handbook WP)
HEADER_DIMS: dict[str, tuple[int, int]] = {
    "twentyten": (940, 198),
    "twentyeleven": (1000, 288),
    "twentytwelve": (960, 250),
    "twentythirteen": (960, 300),
    "twentyfourteen": (1260, 240),
    "twentyfifteen": (954, 1300),
    "twentysixteen": (1200, 280),
    "twentyseventeen": (2000, 1200),
    "twentyeighteen": (1600, 900),
    "twentynineteen": (2000, 600),
    "twentytwenty": (1900, 500),
    "twentytwentyone": (1900, 600),
}


def _fetch(url: str) -> str | None:
    st, body = fetch_url(url, timeout=20)
    if st == 200 and body and len(body) > 500:
        return body.decode("utf-8", "replace")
    return None


def _resolve_url(url: str, base: str) -> str:
    if url.startswith("//"):
        return "https:" + url
    if url.startswith("/"):
        return base.rstrip("/") + url
    return url


def _abs_urls(text: str, slug: str) -> str:
    text = re.sub(
        rf"https?://{re.escape(slug)}\.noblogs\.org/(?:files|wp-content/uploads)/",
        "uploads/", text,
    )
    text = re.sub(r"https?://[^/]+/(?:files|wp-content/uploads)/", "uploads/", text)
    text = re.sub(r"/(?:files|wp-content/uploads)/", "uploads/", text)
    text = re.sub(
        rf"https?://{re.escape(slug)}\.noblogs\.org/",
        "/", text,
    )
    return text


# ------------------------------------------------------------ extraction helpers

def _extract_branding(html: str) -> tuple[str, str]:
    import html as html_mod
    title = ""
    tagline = ""
    m_title = re.search(
        r'<h[1-2][^>]*class=["\'][^"\']*site-title[^"\']*["\'][^>]*>(?:<a[^>]*>)?(.*?)(?:</a>)?</h[1-2]>',
        html, re.S | re.I,
    )
    if m_title:
        title = re.sub(r"<[^>]+>", "", m_title.group(1)).strip()
    if not title:
        m_t = re.search(r"<title>(.*?)</title>", html, re.S | re.I)
        if m_t:
            title = re.split(r"\s+[\u2013\u2014|-]\s+|\s+\u00bb\s+", m_t.group(1).strip())[0].strip()
    m_desc = re.search(r'<p[^>]*class=["\'][^"\']*site-description[^"\']*["\'][^>]*>(.*?)</p>', html, re.S | re.I)
    if m_desc:
        tagline = re.sub(r"<[^>]+>", "", m_desc.group(1)).strip()
    return html_mod.unescape(html_mod.unescape(title)), html_mod.unescape(html_mod.unescape(tagline))


def _find_custom_logo(soup: BeautifulSoup) -> str | None:
    link = soup.find("a", class_=lambda c: c and "custom-logo-link" in c)
    if link:
        img = link.find("img")
        if img and img.get("src"):
            return img["src"].split("?")[0]
    img = soup.find("img", class_=lambda c: c and "custom-logo" in c)
    if img and img.get("src"):
        return img["src"].split("?")[0]
    return None


def _find_header_image(html: str, soup: BeautifulSoup, logo_url: str | None = None) -> str | None:
    if re.search(r"#branding\s+img\s*\{[^}]*display:\s*none", html, re.I):
        return None
    for container in soup.find_all(class_=lambda c: c and any(
        k in c for k in ["wp-custom-header", "custom-header-media", "header-image"]
    )):
        img = container.find("img")
        if img and img.get("src"):
            src = img["src"].split("?")[0]
            if not logo_url or src != logo_url:
                return src
    for bid in ("branding", "headerimg"):
        b = soup.find(id=bid)
        if b:
            for img in b.find_all("img"):
                cls = img.get("class", [])
                if isinstance(cls, str):
                    cls = cls.split()
                if "custom-logo" in cls:
                    continue
                src = img.get("src", "").split("?")[0]
                if src and (not logo_url or src != logo_url):
                    return src
    for img in soup.find_all("img", class_=lambda c: c and "header-image" in c):
        src = img.get("src", "").split("?")[0]
        if src and (not logo_url or src != logo_url):
            return src
    for st in soup.find_all("style"):
        st_id = (st.get("id") or "").lower()
        if any(kw in st_id for kw in ("header", "branding", "custom")):
            m = re.search(
                r"(?:#branding|#header|\.header-image|\.site-header)[^}]*"
                r"background-image:\s*url\(['\"]?(.*?)['\"]?\)",
                st.text,
            )
            if m:
                return m.group(1).split("?")[0]
    return None


def _extract_background(html: str, soup: BeautifulSoup) -> dict:
    """Extrait l'image de fond, la couleur, la répétition, la position, l'attachement et la taille."""
    result: dict = {}
    bg_style = soup.find("style", id=re.compile("custom-background"))
    if not bg_style:
        for s in soup.find_all("style"):
            if ("custom-background" in s.text or "body.custom-background" in s.text
                    or ("body {" in s.text and ("background-image" in s.text or "background-color" in s.text))):
                bg_style = s
                break
    if bg_style and bg_style.text:
        txt = bg_style.text
        m = re.search(r"background-image:\s*url\(['\"]?(.*?)['\"]?\)", txt)
        if m:
            result["background_image"] = m.group(1).strip()
        m = re.search(r"background-color:\s*(#[0-9a-fA-F]{3,6})", txt)
        if m:
            result["background_color"] = m.group(1).lstrip("#")
        for prop in ("background-repeat", "background-position", "background-attachment", "background-size"):
            key = prop.split("-", 1)[1] if "-" in prop else prop
            m = re.search(rf"{prop}:\s*([a-z0-9%_\-]+)", txt)
            if m:
                result[key] = m.group(1)
    return result


def _extract_thememod_colors(html: str, theme: str) -> dict:
    """Couleurs spécifiques par thème (TwentyFifteen header_background_color, sidebar_textcolor, link_color)."""
    colors: dict = {}
    soup = BeautifulSoup(html, "html.parser")
    if theme == "twentyfifteen":
        hbc = stc = ""
        for st in soup.find_all("style"):
            if "Custom Header Background Color" in st.text:
                m = re.search(r"background-color:\s*(#[0-9a-fA-F]{3,6})", st.text)
                if m:
                    hbc = m.group(1)
            if "Custom Sidebar Text Color" in st.text:
                m = re.search(r"color:\s*(#[0-9a-fA-F]{3,6})", st.text)
                if m:
                    stc = m.group(1)
        if hbc:
            colors["header_background_color"] = hbc
        if stc:
            colors["sidebar_textcolor"] = stc
    return colors


def _extract_custom_css(html: str, slug: str) -> str:
    """Récupère tout le CSS personnalisé (Customizer, inline, couleurs) et réécrit les URLs vers uploads/."""
    rules: list[str] = []
    for m in re.finditer(r"<style([^>]*)>(.*?)</style>", html, re.S | re.I):
        attrs, content = m.group(1), m.group(2)
        mid = re.search(r'id=["\']([^"\']+)["\']', attrs)
        block_id = mid.group(1) if mid else ""
        if not content.strip():
            continue
        block_lower = block_id.lower()
        if any(ex in block_lower for ex in EXCLUDE_CSS_IDS):
            continue
        if _BLOCK_INLINE_CSS.match(block_id):
            continue
        if "custom-background" in block_lower:
            continue
        if any(kw in block_lower for kw in KEEP_CSS_KEYWORDS):
            rules.append(f"/* {block_id} */\n{content.strip()}")
    combined = "\n\n".join(rules)
    combined = _SOURCEURL_COMMENT.sub("", combined)
    combined = _abs_urls(combined, slug)
    return combined


def _extract_link_color(custom_css: str) -> str | None:
    m = re.search(r"(?:^|\s)a\s*\{[^}]*color:\s*(#[a-fA-F0-9]{3,6})", custom_css)
    if not m:
        m = re.search(r"\.entry-content\s+a\s*\{[^}]*color:\s*(#[a-fA-F0-9]{3,6})", custom_css)
    return m.group(1) if m else None


# ------------------------------------------------------- sidebar extraction

def _extract_sidebars(html: str, slug: str, theme: str) -> dict:
    """Extrait les widgets de sidebar du HTML original.

    Retourne un dict ``{"sidebars": {sb_id: [widget, ...]}, "storage": {...}}``
    au format compatible injection PHP (WP widget API).
    """
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup.find_all(["script", "noscript"]):
        tag.decompose()

    # Découvrir les widgets
    raw = soup.select(
        "#secondary .widget, #primary-sidebar .widget, #content-sidebar .widget, "
        ".primary-sidebar .widget, .widget-area .widget, .widget-area .widget-container, "
        "#primary .widget-container, #secondary .widget-container, "
        "aside.widget, section.widget, li.widget-container"
    )
    valid = [w for w in raw if not (w.find_parent("article") or w.find_parent(class_="content-area"))]
    if not valid:
        return {"sidebars": {}, "storage": {}}

    is_twentyten = theme == "twentyten" or bool(soup.find(id="wrapper"))
    sidebar_id = "sidebar-1"
    widgets: list[dict] = []

    for w in valid:
        classes = " ".join(w.get("class", []))
        wid_id = w.get("id", "")
        h = w.find(["h2", "h3", "h4", ".widget-title"])
        w_title = h.text.strip() if h else ""

        widget: dict = {"type": "custom_html", "title": w_title, "content": ""}

        if "search" in classes or "widget_search" in wid_id or w.find("form", class_=re.compile("search")):
            widget["type"] = "search"
        elif "widget_categories" in classes or "categories-" in wid_id:
            widget["type"] = "categories"
        elif "widget_archive" in classes or "archives-" in wid_id:
            widget["type"] = "archives"
        elif "widget_recent_entries" in classes or "recent-posts-" in wid_id:
            widget["type"] = "recent-posts"
        elif w.find("iframe", src=re.compile("youtube|vimeo|dailymotion")):
            iframe = w.find("iframe")
            widget["type"] = "media_video"
            widget["url"] = iframe.get("src", "") if iframe else ""
        else:
            tw = w.find(class_=re.compile("textwidget|custom-html-widget"))
            if tw:
                content = tw.decode_contents()
            else:
                w_copy = BeautifulSoup(str(w), "html.parser")
                for hdr in w_copy.find_all(["h1", "h2", "h3", "h4", "h5", "h6"]):
                    hdr.decompose()
                content = w_copy.decode_contents()
            content = _abs_urls(content, slug)
            if "Erreur RSS" in content and "cURL error" in content:
                continue
            if "Pas d'événement" in content and len(content.strip()) < 50:
                continue
            widget["content"] = content.strip()
        widgets.append(widget)

    return {"sidebars": {sidebar_id: widgets}, "storage": {}}


# -------------------------------------------------------- menu extraction

def _extract_menu(html: str, slug: str) -> list[dict]:
    """Extrait la liste de navigation (titres, URLs, sous-menus imbriqués)."""
    soup = BeautifulSoup(html, "html.parser")
    menu_ul = soup.find("ul", class_=lambda c: c and ("nav-menu" in c or "menu" in c))
    if not menu_ul:
        nav = soup.find("nav", class_=lambda c: c and ("navigation" in c or "navbar" in c or "menu" in c))
        if nav:
            menu_ul = nav.find("ul")
    if not menu_ul:
        return []

    items: list[dict] = []
    for li in menu_ul.find_all("li", recursive=False):
        a = li.find("a")
        if not a:
            continue
        href = a.get("href", "")
        href = re.sub(rf"https?://{re.escape(slug)}\.noblogs\.org", "", href)
        item = {"title": a.text.strip(), "href": href}
        sub = li.find("ul", class_="sub-menu")
        if sub:
            item["children"] = []
            for sub_li in sub.find_all("li"):
                sub_a = sub_li.find("a")
                if not sub_a:
                    continue
                shref = sub_a.get("href", "")
                shref = re.sub(rf"https?://{re.escape(slug)}\.noblogs\.org", "", shref)
                item["children"].append({"title": sub_a.text.strip(), "href": shref})
        items.append(item)
    return items


# --------------------------------------------------------------- public API

def extract_fidelity(
    slug: str,
    base_url: str,
    theme: str,
    out_dir: Path,
) -> dict:
    """Analyse le blog original et retourne le dict ``fidelity.json`` complet.

    * ``out_dir`` est le répertoire temporaire dans lequel les fichiers
      fidélité (logo, header-image) sont téléchargés.
    """
    html = _fetch(base_url + "/")
    if not html:
        return {"theme": theme, "error": "homepage_unreachable"}

    soup = BeautifulSoup(html, "html.parser")
    title, tagline = _extract_branding(html)

    # Bannière / logo
    logo_url = _find_custom_logo(soup)
    header_url = _find_header_image(html, soup, logo_url)

    # Thème : recheck depuis le HTML (peut être différent de la détection initiale)
    body_classes = re.search(r"<body[^>]*class=[\"']([^\"']+)[\"']", html)
    if body_classes:
        for c in body_classes.group(1).split():
            if c.startswith("wp-theme-") and "wp-child-theme" not in c:
                theme = c[len("wp-theme-"):]
                break

    header_dims = HEADER_DIMS.get(theme, (940, 198))

    # Background
    bg = _extract_background(html, soup)
    if bg.get("background_image"):
        bg["background_image"] = _resolve_url(bg["background_image"], base_url)

    # CSS + couleurs
    custom_css = _extract_custom_css(html, slug)
    theme_colors = _extract_thememod_colors(html, theme)
    link_color = _extract_link_color(custom_css)

    # Sidebars / widgets
    sidebars = _extract_sidebars(html, slug, theme)

    # Menu
    menu_items = _extract_menu(html, slug)

    # Téléchargement des fichiers médias fidélité
    media_dir = out_dir / "fidelity_media"
    media_dir.mkdir(parents=True, exist_ok=True)
    downloaded: list[str] = []
    for label, url in [("logo", logo_url), ("header-image", header_url)]:
        if url:
            resolved = _resolve_url(url, base_url)
            ext = os.path.splitext(resolved.split("?")[0])[1] or (".png" if label == "logo" else ".jpg")
            dest = media_dir / f"{label}{ext}"
            st, data = fetch_url(resolved, timeout=30)
            if st == 200 and data and len(data) > 100:
                dest.write_bytes(data)
                downloaded.append(label)
                print(f"   [{label}] {dest.name} ({len(data)} octets)")

    # Background image download
    bg_img = bg.get("background_image")
    if bg_img:
        resolved_bg = _resolve_url(bg_img, base_url)
        bg_ext = os.path.splitext(resolved_bg.split("?")[0])[1]
        bg_dest = media_dir / ("background" + bg_ext)
        st, data = fetch_url(resolved_bg, timeout=30)
        if st == 200 and data and len(data) > 100:
            bg_dest.write_bytes(data)
            downloaded.append("background-image")
            print(f"   [background-image] {bg_dest.name} ({len(data)} octets)")
            bg["background_image_local"] = bg_dest.name

    return {
        "theme": theme,
        "title": title,
        "tagline": tagline,
        "header_image": {
            "url": header_url,
            "width": header_dims[0],
            "height": header_dims[1],
        },
        "logo": {"url": logo_url},
        "background": bg,
        "custom_css": custom_css,
        "theme_colors": theme_colors,
        "link_color": link_color,
        "sidebars": sidebars["sidebars"],
        "menu_items": menu_items,
        "downloaded_media": downloaded,
    }