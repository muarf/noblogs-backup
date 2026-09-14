# Republication locale (identique à l'original)

**Objectif** : restaurer votre blog à l'identique sur un WordPress que vous contrôlez (VPS, ordinateur, Docker).

---

## Option A — Assistant automatique (recommandé)

Décompressez votre ZIP, puis :

```bash
./restore.sh
```

L'assistant :
1. Détecte vos installations WordPress
2. Vous fait choisir la cible
3. Demande la nouvelle URL de votre site
4. Restaure : médias → thème → articles → menus → CSS → couleurs

**Prérequis** : WordPress déjà installé + **WP-CLI** (`wp` en ligne de commande).

> **Pas de WordPress sous la main ?** L'assistant détecte automatiquement les
> installations présentes ; s'il n'en trouve aucune et que **Docker** est installé,
> il propose **« Auto-installer WordPress avec Docker »** : une base MariaDB, WP-CLI
> et un serveur web sont lancés dans des conteneurs, WordPress est installé puis
> restauré. Vous retrouvez ensuite votre blog sur `http://localhost:8080`.
>
> Le site restauré vit dans `backups/restores/<slug>` et survive à la fermeture
> de l'assistant : rejouez `republier` pour le réinitialiser ou le relire.

### Installer WordPress + WP-CLI rapidement (Ubuntu)

```bash
# WordPress via Docker (le plus simple)
docker run -d --name wordpress \
  -p 8080:80 \
  -e WORDPRESS_DB_HOST=db \
  wordpress:latest

# WP-CLI
curl -O https://raw.githubusercontent.com/wp-cli/builds/gh-pages/phar/wp-cli.phar
chmod +x wp-cli.phar
sudo mv wp-cli.phar /usr/local/bin/wp
```

Puis `./restore.sh` avec `WP=/chemin/vers/wordpress`.

---

## Option B — Interface WordPress (sans WP-CLI)

1. **Médias** : copiez le dossier `uploads/` dans `wp-content/uploads/` de votre WordPress.
2. **Thème** : copiez `theme/nom-du-theme/` dans `wp-content/themes/`, activez-le dans **Apparence → Thèmes**.
3. **Articles** : **Outils → Importer → WordPress** → uploadez `wordpress-export.xml`.
4. **Fidélité** (widgets, menus, CSS) : nécessite WP-CLI :
   ```bash
   wp eval-file restore_parity.php --path=/var/www/html
   ```

---

## Option C — Via noblogs (depuis le dossier de l'outil)

```bash
./noblogs republier backups/monblog-noblogs-backup.zip
# Choisir [2] WordPress local
```

---

## Stock local (thèmes & plugins du réseau)

Chaque archive est **auto-contenue** : elle embarque le thème actif du blog
(depuis le store), les plugins du réseau et les mu-plugins. Aucun accès réseau
n'est nécessaire au moment du backup ni de la restauration.

Le store est un cache local qui matérialise l'intégralité des thèmes, plugins
et mu-plugins du réseau NoBlogs :

```bash
python -m backup wizard store sync   # rsync depuis le mirroir (recommandé, complet)
python -m backup wizard store git    # thèmes + mu-plugins depuis git.inventati.org
python -m backup wizard store        # état du store
```

> **Vieille archive incomplète ?** Un thème réduit à `style.css` provoque une
> page blanche (HTTP 200 vide) à l'affichage. Complétez l'archive depuis le store
> sans re-scraper le blog :
> ```bash
> python -m backup upgrade backups/monblog-noblogs-backup.zip
> ```
> Puis relancez la republication locale.

---

## Local sur votre ordinateur (macOS / Ubuntu)

**Local WP** (https://localwp.com) — application gratuite, installe WordPress en un clic :

1. Installez Local
2. Créez un site vide
3. Ouvrez le terminal du site dans Local
4. Copiez votre ZIP dans le dossier du site
5. Lancez `./restore.sh` en pointant `WP` vers le dossier WordPress de Local

---

## Après la restauration

- Vérifiez quelques articles et images
- Consultez `metadata.json` pour le nombre d'articles/médias attendus
- Si le thème est incomplet, vérifiez le nom dans `metadata.json` → `theme`
  et complétez l'archive avec `python -m backup upgrade <archive.zip>`
