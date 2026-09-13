"""package.py — Emballage de la sauvegarde en ZIP autonome complet.

Produit une archive redéployable à l'identique sur n'importe quelle
installation WordPress :
* ``wordpress-export.xml``  – export WXR 1.2 (articles, pages, catégories)
* ``uploads/``              – tous les médias
* ``theme/<theme>/``        – le thème WordPress actif
* ``fidelity.json``         – sidebars, menus, CSS, couleurs, header image
* ``restore.sh``            – script de restauration complète
* ``restore_parity.php``    – logique WP de fidélité (widgets, menus, theme_mods)
* ``README.md``, ``metadata.json``
"""
from __future__ import annotations

import shutil
import tempfile
import zipfile
from datetime import datetime
from pathlib import Path

_PACKAGE_DIR = Path(__file__).resolve().parent
_DOCS_DIR = _PACKAGE_DIR.parent / "docs"


def human_size(num: int) -> str:
    for unit in ["o", "Ko", "Mo", "Go", "To"]:
        if num < 1024:
            return f"{num:.1f} {unit}"
        num /= 1024
    return f"{num:.1f} To"


def _write_readme(stage: Path, slug: str, original_url: str, has_fidelity: bool, has_theme: bool) -> None:
    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    fidelity_note = ""
    if has_fidelity:
        fidelity_note = """- **`fidelity.json`** — sidebars, menus, CSS personnalisé, couleurs, bannière (fidélité visuelle).
- **`restore_parity.php`** — application automatique de la fidélité via WP-CLI.
"""
    theme_note = ""
    if has_theme:
        theme_note = """- **`theme/`** — le thème WordPress actif du blog original.
"""
    (stage / "README.md").write_text(f"""# Sauvegarde NoBlogs — {slug}

Export complet du blog **{slug}**, réalisé le {now}.
Source : {original_url}

## Contenu
* **`wordpress-export.xml`** — export WXR 1.2 (articles, pages, catégories), compatible WordPress.
* **`uploads/`** — tous les médias, à placer dans `wp-content/uploads/`.
{fidelity_note}{theme_note}* **`restore.sh`** — restauration complète en une commande (WP-CLI).
* **`metadata.json`** — informations sur la sauvegarde.

## Restauration complète avec `restore.sh` (recommandé, VPS / Docker / WP-CLI)
Dézippez l'archive, puis lancez :
```bash
./restore.sh                              # assistant interactif (détection auto du WordPress)
WP=/var/www/html ./restore.sh             # cible explicite
WP=/var/www/html URL=https://monsite.org ./restore.sh  # avec remplacement d'URLs
```
L'assistant détecte automatiquement vos installations WordPress, propose un menu
numéroté, vous demande la nouvelle URL, affiche un **résumé avant exécution** puis
applique : médias → thème → import WXR → URLs → sidebars/menus/CSS/couleurs →
`<!--more-->` → nettoyage (contenu par défaut WP supprimé avec précaution).

## Restauration manuelle (interface WordPress)
1. Ouvrez **Outils > Importer > WordPress** (installez l'extension si demandé).
2. Importez `wordpress-export.xml` avec l'option *"Télécharger et importer les fichiers joints"*.
3. Copiez le contenu de `uploads/` dans `wp-content/uploads/`.
4. Copiez `theme/*` dans `wp-content/themes/` et activez le thème.
5. Appliquez la fidélité : `wp eval-file restore_parity.php --path=/votre/wordpress`
""", encoding="utf-8")


def package_backup(
    slug: str,
    exports_dir: Path,
    wxr_path: Path,
    uploads_dir: Path,
    original_url: str,
    title: str | None = None,
    theme: str | None = None,
    posts_count: int = 0,
    pages_count: int = 0,
    media_stats: dict | None = None,
    fidelity: dict | None = None,
    theme_dir: Path | None = None,
) -> Path:
    """Crée l'archive ZIP finale et retourne son chemin."""
    media_stats = media_stats or {}
    fidelity = fidelity or {}
    exports_dir.mkdir(parents=True, exist_ok=True)
    stage = Path(tempfile.mkdtemp(prefix=f"noblogs_backup_{slug}_"))
    zip_file = exports_dir / f"{slug}-noblogs-backup.zip"
    try:
        # 1. WXR
        (stage / "wordpress-export.xml").write_bytes(wxr_path.read_bytes())

        # 2. Médias
        if uploads_dir.exists() and any(uploads_dir.iterdir()):
            shutil.copytree(uploads_dir, stage / "uploads", dirs_exist_ok=True)

        # 3. Thème
        has_theme = False
        if theme_dir is not None and theme_dir.exists() and any(theme_dir.iterdir()):
            shutil.copytree(theme_dir, stage / "theme" / theme_dir.name, dirs_exist_ok=True)
            has_theme = True

        # 4. Fidélité : JSON + scripts + médias fidélité
        has_fidelity = bool(fidelity)
        media_src = wxr_path.parent / "fidelity_media"
        if fidelity:
            (stage / "fidelity.json").write_text(
                __import__("json").dumps(fidelity, indent=2, ensure_ascii=False),
                encoding="utf-8",
            )
            if media_src.exists() and any(media_src.iterdir()):
                shutil.copytree(media_src, stage / "fidelity_media", dirs_exist_ok=True)

        # 5. Scripts de restauration + README + métadonnées
        shutil.copy(_PACKAGE_DIR / "restore.sh", stage / "restore.sh")
        shutil.copy(_PACKAGE_DIR / "restore_parity.php", stage / "restore_parity.php")
        shutil.copy(_PACKAGE_DIR / "media_relink.php", stage / "media_relink.php")
        (stage / "restore.sh").chmod(0o755)
        _write_readme(stage, slug, original_url, has_fidelity, has_theme)

        for guide in (
            "GUIDE-MILITANTE.md",
            "GUIDE-WORDPRESS-COM.md",
            "GUIDE-LOCAL.md",
        ):
            src = _DOCS_DIR / guide
            if src.exists():
                shutil.copy(src, stage / guide)

        media_success = sum(v for k, v in media_stats.items() if k not in ("failed", "urls"))
        (stage / "metadata.json").write_text(
            __import__("json").dumps(
                {
                    "slug": slug,
                    "title": title,
                    "theme": theme,
                    "original_url": original_url,
                    "created": datetime.now().isoformat(),
                    "posts_count": posts_count,
                    "pages_count": pages_count,
                    "media": media_stats,
                    "media_success": media_success,
                    "fidelity": bool(fidelity),
                    "theme_bundled": has_theme,
                },
                indent=2,
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )

        # 6. Compression ZIP
        if zip_file.exists():
            zip_file.unlink()
        with zipfile.ZipFile(zip_file, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as zf:
            for f in sorted(stage.rglob("*")):
                if f.is_file():
                    zf.write(f, f.relative_to(stage))
        return zip_file
    finally:
        shutil.rmtree(stage, ignore_errors=True)