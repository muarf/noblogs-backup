# Republication en site Hugo statique (sans WordPress)

**Objectif** : publier votre blog comme un **site statique** léger, sans
WordPress, 100 % hors-ligne. Rendu visuel fidèle : la vraie CSS de votre blog
(vendue dans le ZIP via `theme/`) + l'habillage du thème nblogs + la fidélité
capturée (`fidelity.json` — menus, sidebar, custom CSS, logo, bannière).

---

## Flux automatique (recommandé)

Depuis le dossier de l'outil :

```bash
./noblogs republier backups/monblog-noblogs-backup.zip
# Choisir [3] Site Hugo statique
```

Ou juste après une sauvegarde :

```bash
./noblogs sauvegarder monblog
# Puis [3] Site Hugo statique
```

L'assistant :
1. Décompresse le ZIP et convertit `wordpress-export.xml` → dépôt Hugo
   (`backups/hugo/<slug>/`) : articles/pages en Markdown + front matter YAML,
   médias → `static/images/`, permaliens auto-détectés, menu + sidebar fidèles.
2. Vérifie le binaire **hugo** :
   * s'il est déjà installé (`command -v hugo`) → utilisation directe ;
   * sinon il est **téléchargé** (GitHub, ~70 Mo, dans `backups/hugo/bin/`,
     comme on lance Docker la première fois) ;
   * sinon **Docker** (`klakegg/hugo`) ;
   * sinon annulation avec renvoi vers ce guide.
3. Construit le site dans `backups/hugo/<slug>/public/`.
4. Le sert sur `http://localhost:8092` (Ctrl+C pour arrêter).

Le dépôt est **durable** : rejouez `republier` pour le reconstruire (**R**)
ou le rouvrir (**O**).

---

## Sans l'assistant (à la main)

```bash
# 1. Dézipper l'archive
unzip backups/monblog-noblogs-backup.zip -d /tmp/monblog

# 2. Convertir en dépôt Hugo
python -m backup wizard republier backups/monblog-noblogs-backup.zip  # ou :
python -c "from backup.hugo import build_site; print(build_site(Path('/tmp/monblog'), 'monblog', Path('backups/hugo/monblog')))"

# 3. Construire
hugo --source backups/hugo/monblog          # → backups/hugo/monblog/public/

# 4. Ouvrir en local
python3 -m http.server 8092 --directory backups/hugo/monblog/public
# → http://localhost:8092
```

---

## Ce que produit le convertisseur

* `content/posts/` et `content/pages/` — Markdown + front matter YAML
  (title, date, slug, author, categories, tags, aliases). Le **HTML
  WordPress est conservé tel quel** dans le corps (fidélité visuelle).
* `static/images/` — les médias du blog ; les URLs `uploads/` du contenu
  sont réécrites vers `/images/…`.
* `themes/nblogs/` — thème nblogs **vendoré** (version de l'outil en priorité,
  sinon celle du ZIP `hugo/nblogs/`), aucun accès réseau.
* `static/css/profiles/<thème>/` — la vraie CSS du blog depuis `theme/` :
  le profil **minimalism** reproduit le DOM WordPress attendu
  (`#page`, `#header`, `#content.narrowcolumn`, `#sidebar`, `.post`, …).
* `static/css/blog-custom.css` — custom CSS, logo, bannière, fond, couleurs
  de `fidelity.json`.
* `layouts/partials/sidebars/<slug>.html` — la sidebar réelle du blog.
* `config.toml` — titre, tagline, permaliens, `[[menu.main]]` (fidèle).

## Déployer le site (publication en ligne)

Le dossier `backups/hugo/<slug>/public/` est un site statique autonome :

* **Neocities / GitHub Pages / Codeberg Pages / Forgejo** : uploadez le
  contenu de `public/`.
* **VPS** : copiez `public/` dans le dossier servi par nginx, ex.
  `rsync -a backups/hugo/<slug>/public/ vps:/var/www/monblog/`.
* **Tor (.onion)** : sert le contenu de `public/` avec nginx, comme les
  autres blogs hébergés (un seul site statique, pas de PHP).

Aucun WP-CLI, aucune base de données, aucun conteneur n'est requis à la
publication.

---

## Notes

* **Fichier `metadata.json`** → `"hugo_theme": true` : le ZIP embarque le thème
  nblogs. Une vieille archive peut être complétée sans re-scraper :
  `python -m backup upgrade <archive.zip>`.
* Port personnalisable : `NOBLOGS_HUGO_PORT=8500 ./noblogs republier …`
* Le build du site statique est **entièrement local** : seule la première
  exécution (binaire hugo) touche au réseau.