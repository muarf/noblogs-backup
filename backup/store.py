"""store.py — Stock local vendored des thèmes/plugins/mu-plugins du réseau NoBlogs.

La source de vérité est le mirroir (thèmes, plugins, mu-plugins déployés sur le
réseau), matérialisable localement par ``noblogs store sync`` (rsync depuis la
machine mirroir) ou, pour les thèmes et ai-mu-plugins, par ``noblogs store git``
(téléchargement direct des dépôts git.inventati.org/noblogs).

Le store sert UNIQUEMENT à la constitution du ZIP au moment du backup : chaque
archive embarque le thème actif + les plugins + les mu-plugins du réseau. Le
restore, lui, ne lit que le ZIP (aucune dépendance réseau).
"""
from __future__ import annotations

import io
import json
import os
import shutil
import subprocess
import warnings
import zipfile
from pathlib import Path

DEFAULT_STORE = Path(__file__).resolve().parent.parent / "backups" / "store"

GITLAB = "https://git.inventati.org"
MIRROR_ROOT = "/var/www/wordpress-core/wp-content"
MIRROR_USER = os.getenv("NOBLOGS_MIRROR_USER", "ubuntu")
MIRROR_HOST = os.getenv("NOBLOGS_MIRROR_HOST", "bigarm")

# Dépôts git.inventati.org/noblogs : (projet, branche, dossier cible)
GIT_REPOS = [
    ("themes-misc", "master", "themes"),
    ("themes-legacy", "main", "themes"),
    ("themes-child", "master", "themes"),
    ("ai-mu-plugins", "master", "mu-plugins"),
]

# Un thème sans template (index.php / index.html) → page blanche 200 à
# l'affichage. Le restore doit alors NE PAS l'activer.
TEMPLATE_NAMES = {"index.php", "index.html", "templates/index.html", "templates/index.php"}


def store_dir() -> Path:
    """Racine du store local (surchargeable via NOBLOGS_STORE)."""
    root = os.getenv("NOBLOGS_STORE")
    return Path(root).expanduser() if root else DEFAULT_STORE


def themes_root() -> Path:
    return store_dir() / "themes"


def plugins_root() -> Path:
    return store_dir() / "plugins"


def mu_plugins_root() -> Path:
    return store_dir() / "mu-plugins"


def theme_dir(theme_slug: str) -> Path | None:
    """Dossier du thème dans le store (None si absent)."""
    d = themes_root() / theme_slug
    if d.is_dir() and any(d.iterdir()):
        return d
    return None


def plugins_list() -> list[str]:
    """Slugs (noms de dossiers) des plugins présents dans le store."""
    root = plugins_root()
    if not root.is_dir():
        return []
    return sorted(p.name for p in root.iterdir() if p.is_dir() and any(p.iterdir()))


def mu_plugins_list() -> list[str]:
    root = mu_plugins_root()
    if not root.is_dir():
        return []
    return sorted(p.name for p in root.iterdir() if p.is_file())


def has_template(theme_slug: str) -> bool:
    """Le thème du store a-t-il un fichier de template (≠ page blanche) ?"""
    d = theme_dir(theme_slug)
    if not d:
        return False
    for name in TEMPLATE_NAMES:
        if (d / name).is_file():
            return True
    if (d / "templates").is_dir() and any((d / "templates").rglob("*.html")):
        return True
    return False


def resolve_theme(theme_slug: str, dest_dir: Path) -> Path | None:
    """Copie le thème complet depuis le store dans ``dest_dir/<slug>/``.

    Retourne le dossier créé, ou None si le thème est absent du store.
    Aucun accès réseau : le store contient l'intégralité du réseau NoBlogs.
    """
    dest_dir.mkdir(parents=True, exist_ok=True)
    src = theme_dir(theme_slug)
    if not src:
        return None
    target = dest_dir / theme_slug
    if target.exists():
        shutil.rmtree(target, ignore_errors=True)
    shutil.copytree(src, target)
    return target


# ------------------------------------------------------------------------- git

def _gitlab_zip(project: str, branch: str, dest: Path) -> bool:
    """Télécharge l'archive ZIP d'un projet GitLab (certificat auto-signé)."""
    import requests

    url = f"{GITLAB}/api/v4/projects/noblogs%2F{project}/repository/archive.zip"
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", requests.packages.urllib3.exceptions.InsecureRequestWarning)
            r = requests.get(url, params={"sha": branch}, timeout=120, verify=False)
        if r.status_code != 200:
            print(f"  [store] {project}: HTTP {r.status_code}")
            return False
        with zipfile.ZipFile(io.BytesIO(r.content)) as zf:
            names = zf.namelist()
            if not names:
                return False
            top = sorted({n.split("/", 1)[0] for n in names if "/" in n})
            zf.extractall(dest)
        # GitLab empaquette dans "<projet>-<branche>-<sha>/" → on remonte d'un niveau.
        for sub in top:
            sub_path = dest / sub
            if not sub_path.is_dir():
                continue
            for item in sub_path.iterdir():
                target = dest / item.name
                if target.exists():
                    if target.is_dir():
                        shutil.rmtree(target, ignore_errors=True)
                    else:
                        target.unlink(missing_ok=True)
                shutil.move(str(item), str(target))
            shutil.rmtree(sub_path, ignore_errors=True)
        return True
    except Exception as e:
        print(f"  [store] {project}: échec — {e}")
        return False


def cmd_git() -> int:
    """Télécharge les thèmes + ai-mu-plugins depuis git.inventati.org/noblogs."""
    for project, branch, target in GIT_REPOS:
        print(f"\n  Téléchargement du dépôt {project} ({branch})…")
        dest = store_dir() / target
        ok = _gitlab_zip(project, branch, dest)
        print(f"  [store] {'✓' if ok else '✗'} {project} → {dest}")
    print("\n  Notes :")
    print("    - footnotation, multisite-custom-css et les mu-plugins déployés")
    print("      ne sont PAS dans git.inventati.org : lancez `store sync` pour le")
    print("      set complet (mirroir du réseau).")
    return 0


# ----------------------------------------------------------------------- sync

def _mirror() -> str:
    return f"{MIRROR_USER}@{MIRROR_HOST}"


def cmd_sync() -> int:
    """Rsync du mirroir : thèmes + plugins + mu-plugins → store local."""
    if not shutil.which("rsync"):
        print("  ✗ rsync introuvable sur cette machine.")
        return 1
    base_local = store_dir()
    base_remote = f"{_mirror()}:{MIRROR_ROOT}"
    ok = 0
    for sub in ("themes", "plugins", "mu-plugins"):
        print(f"\n  Synchronisation {sub}…")
        dest = base_local / sub
        dest.mkdir(parents=True, exist_ok=True)
        cmd = ["rsync", "-a", "--delete", "--timeout=120", f"{base_remote}/{sub}/", f"{dest}/"]
        r = subprocess.run(cmd, capture_output=True, text=True)
        if r.returncode == 0:
            print(f"  [store] ✓ {sub} → {dest}")
            ok += 1
        else:
            print(f"  [store] ✗ {sub}:")
            print((r.stderr or r.stdout).rstrip())
    print(f"\n  {ok}/3 dossiers synchronisés depuis {_mirror()}")
    return 0 if ok == 3 else 1


# -------------------------------------------------------------------- command

def upgrade_zip(zip_path: Path) -> dict:
    """Complète un ZIP existant avec thème + plugins + mu-plugins du store.

    Le blog peut être mort : on ne re-scrape rien, on ré-empaquette seulement
    depuis le store local (''tout dans le zip''). Met aussi à jour
    ``metadata.json`` (theme_complete, plugins, mu_plugins).

    Retourne un résumé {theme, theme_complete, plugins, mu_plugins}.
    """
    meta: dict = {}
    with zipfile.ZipFile(zip_path) as zf:
        if "metadata.json" in zf.namelist():
            meta = json.loads(zf.read("metadata.json").decode("utf-8"))

    theme_slug = meta.get("theme") or ""
    store_theme = theme_dir(theme_slug) if theme_slug else None
    store_plugins = plugins_root()
    store_mu = mu_plugins_root()

    prefix_theme = f"theme/{theme_slug}/" if theme_slug else "__none__"
    replaced = ("theme/", "plugins/", "mu-plugins/")

    tmp = zip_path.with_name(zip_path.name + ".tmp")
    with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as zf_out:
        with zipfile.ZipFile(zip_path) as zf_in:
            for n in zf_in.namelist():
                if n.endswith("/") or n in ("metadata.json",) or n.startswith(replaced):
                    continue
                zf_out.writestr(n, zf_in.read(n))
        if store_theme:
            for f in sorted(store_theme.rglob("*")):
                if f.is_file():
                    zf_out.write(f, f"{prefix_theme}{f.relative_to(store_theme)}")
        if store_plugins.is_dir():
            for entry in sorted(store_plugins.iterdir()):
                if not (entry.is_dir() and any(entry.iterdir())):
                    continue
                for f in sorted(entry.rglob("*")):
                    if f.is_file():
                        zf_out.write(f, f"plugins/{entry.name}/{f.relative_to(entry)}")
        if store_mu.is_dir():
            for f in sorted(store_mu.glob("*")):
                if f.is_file():
                    zf_out.write(f, f"mu-plugins/{f.name}")

        hugo_theme = Path(__file__).resolve().parent / "hugo" / "nblogs"
        has_hugo = False
        if hugo_theme.is_dir() and any(hugo_theme.rglob("*.html")):
            for f in sorted(hugo_theme.rglob("*")):
                if f.is_file():
                    zf_out.write(f, f"hugo/nblogs/{f.relative_to(hugo_theme)}")
            has_hugo = True

        meta["theme_bundled"] = bool(store_theme)
        meta["theme_complete"] = bool(store_theme) and has_template(theme_slug)
        meta["plugins"] = plugins_list()
        meta["mu_plugins"] = mu_plugins_list()
        meta["hugo_theme"] = has_hugo
        zf_out.writestr("metadata.json", json.dumps(meta, indent=2, ensure_ascii=False))

    tmp.replace(zip_path)
    return {
        "theme": theme_slug,
        "theme_complete": meta["theme_complete"],
        "plugins": meta["plugins"],
        "mu_plugins": meta["mu_plugins"],
    }


def store_status() -> str:
    root = store_dir()
    n_themes = sum(1 for p in (root / "themes").iterdir() if p.is_dir() and any(p.iterdir())) \
        if (root / "themes").is_dir() else 0
    n_plugins = len(plugins_list())
    n_mu = len(mu_plugins_list())
    return f"{n_themes} thèmes — {n_plugins} plugins — {n_mu} mu-plugins"


def store_main(argv: list[str] | None = None) -> int:
    """Sous-commande ``noblogs store [sync|git|status]``."""
    args = list(argv) if argv is not None else []
    action = args[0] if args else "status"

    if action == "sync":
        return cmd_sync()
    if action == "git":
        return cmd_git()
    if action != "status":
        print(f"  ✗ Sous-commande inconnue : {action}")
        print("    Usage : noblogs store <sync|git|status>")

    print(f"  Store : {store_dir()}")
    print(f"    {store_status()}")
    print("\n  Commandes :")
    print("    noblogs store sync     rsync du mirroir (set complet, recommandé)")
    print("    noblogs store git      thèmes + ai-mu-plugins depuis git.inventati.org")
    return 0