"""theme_download.py — Scrape du thème WordPress actif depuis le blog (fallback).

Le chemin normal de constitution du thème est le **store local** (``store.py``) :
il contient l'intégralité des thèmes du réseau NoBlogs (dont les thèmes officiels),
et le ZIP embarque le thème complet. Ce module ne sert qu'au dernier recours quand
le thème n'est pas dans le store (blog au thème atypique) : il récupère les
fichiers clés (style.css, index.php) directement depuis le site ou Wayback.

Aucun recours à l'API WordPress.org.
"""
from __future__ import annotations

from pathlib import Path

from .http import fetch_url

# Fichiers tentés dans l'ordre ; seuls ceux disponibles sont récupérés.
THEME_FILES = (
    "style.css",
    "index.php",
    "functions.php",
    "header.php",
    "footer.php",
    "sidebar.php",
    "screenshot.png",
)


def download_theme_from_site(
    theme_slug: str,
    base_url: str,
    dest_dir: Path,
    use_wayback: bool = True,
) -> Path | None:
    """Récupère les fichiers essentiels du thème depuis le blog d'origine.

    Retourne le dossier ``dest_dir/<slug>/`` (avec ce qui a pu être récupéré),
    ou None si rien n'a été obtenu.
    """
    theme_dir = dest_dir / theme_slug
    theme_dir.mkdir(parents=True, exist_ok=True)

    got_any = False
    for fname in THEME_FILES:
        url = f"{base_url}/wp-content/themes/{theme_slug}/{fname}"
        st, data = fetch_url(url, timeout=20)
        if st == 200 and data:
            theme_dir.joinpath(fname).write_bytes(data)
            got_any = True
            continue
        if use_wayback:
            wb_url = f"https://web.archive.org/web/2025id_/{url}"
            st_wb, data_wb = fetch_url(wb_url, timeout=30)
            if st_wb == 200 and data_wb and b"<html" not in data_wb[:300].lower():
                theme_dir.joinpath(fname).write_bytes(data_wb)
                got_any = True

    if not got_any:
        return None
    print(f"  [thème] fallback : {theme_slug} partiellement récupéré depuis le site")
    return theme_dir