#!/bin/bash
# ============================================================================
# restore.sh — Restauration complète d'un backup NoBlogs sur WordPress
#
# Usage :
#   ./restore.sh                              # mode interactif (TTY)
#   WP=/var/www/html ./restore.sh             # dossier WordPress cible
#   WP=/var/www/html URL=https://x.fr ./restore.sh  # avec remplacement d'URL
#   WP=/var/www/html SKIP_WXR=1 ./restore.sh  # sauter l'import WXR
#
# Prérequis : WordPress installé (wp-load.php), WP-CLI fortement recommandé.
# ============================================================================
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

WP="${WP:-}"
URL="${URL:-}"
SKIP_WXR="${SKIP_WXR:-0}"

# --- Couleurs ---
G="\033[0;32m" ; R="\033[0;31m" ; Y="\033[1;33m" ; C="\033[0;36m" ; B="\033[1m" ; N="\033[0m"
ok()   { echo -e "  ${G}[✓]${N} $*"; }
warn() { echo -e "  ${Y}[!]${N} $*"; }
err()  { echo -e "  ${R}[✗]${N} $*"; }
info() { echo -e "  ${C}[i]${N} $*"; }

# --- Chargement métadonnées (sans dépendance à python3) ---
BACKUP_TITLE="" ; BACKUP_SLUG="" ; BACKUP_THEME="" ; BACKUP_POSTS=0 ; BACKUP_PAGES=0
BACKUP_MEDIA=0 ; BACKUP_FIDELITY="" ; BACKUP_ORIGINAL_URL=""
META_JSON="$SCRIPT_DIR/metadata.json"

meta() { # $1 = clé JSON
  sed -n "s/.*\"$1\"[[:space:]]*:[[:space:]]*\"\{0,1\}\([^\",]*\)\"\{0,1\}.*/\1/p" "$META_JSON" 2>/dev/null | head -1
}

if [ -f "$META_JSON" ]; then
  if command -v python3 >/dev/null 2>&1; then
    META="$META_JSON" python3 - > /tmp/_noblogs_meta.$$ << 'META_PY'
import json, os
d = json.load(open(os.environ["META"], encoding="utf-8"))
for k, v in {
 "BACKUP_SLUG": str(d.get("slug", "")),
 "BACKUP_TITLE": str(d.get("title") or ""),
 "BACKUP_THEME": str(d.get("theme") or ""),
 "BACKUP_POSTS": str(d.get("posts_count", 0)),
 "BACKUP_PAGES": str(d.get("pages_count", 0)),
 "BACKUP_MEDIA": str(d.get("media_success", 0)),
 "BACKUP_FIDELITY": "oui" if d.get("fidelity") else "",
 "BACKUP_ORIGINAL_URL": str(d.get("original_url", "")),
}.items():
    print(f"{k}='{v}'")
META_PY
    source /tmp/_noblogs_meta.$$ 2>/dev/null || true
    rm -f /tmp/_noblogs_meta.$$
  else
    BACKUP_SLUG=$(meta slug)
    BACKUP_TITLE=$(meta title)
    BACKUP_THEME=$(meta theme)
    BACKUP_POSTS=$(meta posts_count)
    BACKUP_PAGES=$(meta pages_count)
    BACKUP_MEDIA=$(meta media_success)
    BACKUP_FIDELITY=$(meta fidelity)
    BACKUP_ORIGINAL_URL=$(meta original_url)
  fi
fi
[ -z "$BACKUP_SLUG" ] && BACKUP_SLUG=$(basename "$SCRIPT_DIR" | sed 's/-noblogs-backup$//')

# --- Détection de WordPress ---
detect_wp() {
    local -a found=()
    local -A seen=()
    local dir

    for dir in "$PWD" "$PWD/wordpress" "$PWD/public" "$PWD/html" \
               /var/www/html /var/www /srv/www /srv/www/html \
               "$HOME/public_html" "$HOME/www"; do
        if [ -f "$dir/wp-load.php" ] && [ -z "${seen[$dir]:-}" ]; then
            found+=("$dir")
            seen[$dir]=1
        fi
    done

    while IFS= read -r f; do
        dir=$(dirname "$f")
        if [ -z "${seen[$dir]:-}" ]; then
            found+=("$dir")
            seen[$dir]=1
        fi
    done < <(find /var/www /tmp "$HOME" -maxdepth 3 -name wp-load.php -type f 2>/dev/null || true)

    if [ "${#found[@]}" -gt 0 ]; then
        printf '%s\n' "${found[@]}"
    fi
}

# --- Méthode wp avec wrapper ---
WP_SUDO=""
WP_DOCKER="${WP_DOCKER:-}"
WP_CT_PATH="${WP_CT_PATH:-/var/www/html}"
CP="cp"
MKDIR="mkdir -p"
WPQ() {
    if [ -n "$WP_DOCKER" ]; then
        local -a _na=()
        local _a
        for _a in "$@"; do
            case "$_a" in
                "$SCRIPT_DIR"/*) _na+=("${WP_CT_PATH}/${_a#"$SCRIPT_DIR"/}") ;;
                *) _na+=("$_a") ;;
            esac
        done
        docker exec "$WP_DOCKER" php -d memory_limit=512M /usr/local/bin/wp \
            --path="$WP_CT_PATH" --allow-root "${_na[@]}"
    elif [ -n "$WP_SUDO" ]; then
        $WP_SUDO wp --path="$WP" --allow-root "$@"
    else
        wp --path="$WP" --allow-root "$@"
    fi
}

setup_wp_cmd() {
    if [ -n "$WP_DOCKER" ]; then
        if command -v docker &>/dev/null && docker ps -a --format '{{.Names}}' | grep -qx "$WP_DOCKER"; then
            ok "WP-CLI disponible (conteneur $WP_DOCKER)"
        else
            warn "Conteneur $WP_DOCKER introuvable — l'import WXR et la fidélité seront manuels."
            WP_DOCKER=""
        fi
    elif have_wp; then
        if id -u &>/dev/null && [ "$(id -u)" -ne 0 ] && [ ! -w "$WP/wp-content" ]; then
            if command -v sudo &>/dev/null; then
                WP_SUDO="sudo"
                ok "WP-CLI disponible (via sudo)"
            else
                warn "WP-CLI trouvé mais permissions insuffisantes (pas de sudo)."
                warn "L'import WXR échouera sans droits root."
            fi
        else
            WP_SUDO=""
            ok "WP-CLI disponible"
        fi
    else
        warn "WP-CLI non trouvé. L'import WXR et la fidélité seront manuels."
    fi
    if [ -n "$WP_SUDO" ]; then
        CP="sudo cp"
        MKDIR="sudo mkdir -p"
    fi
}

# Vrai si un client WP-CLI est disponible (hôte ou conteneur Docker).
have_wp() {
    [ -n "$WP_DOCKER" ] || command -v wp >/dev/null 2>&1
}

# --- Installation automatique de WordPress via Docker (dans ce dossier) ---
auto_install_docker() {
    info "Installation automatique de WordPress avec Docker…"
    command -v docker >/dev/null 2>&1 || { err "Docker n'est pas installé sur cette machine."; return 1; }
    docker info >/dev/null 2>&1 || { err "Docker ne semble pas démarré (lancez-le puis réessayez)."; return 1; }

    local NB_NET="noblogs-net" NB_DB="noblogs-db" NB_CLI="noblogs-cli" NB_WEB="noblogs-web"
    local NB_PORT="${NOBLOGS_PORT:-8080}" NB_WP=""
    local UID_NUM="" GID_NUM=""

    # Le dossier courant (celui du backup, où tourne restore.sh) devient la racine du site.
    NB_WP="$SCRIPT_DIR"

    docker network inspect "$NB_NET" >/dev/null 2>&1 || docker network create "$NB_NET" >/dev/null

    if ! docker ps -a --format '{{.Names}}' | grep -qx "$NB_DB"; then
        ok "Démarrage de MariaDB…"
        docker run -d --name "$NB_DB" --network "$NB_NET" \
            -e MARIADB_ROOT_PASSWORD=root -e MARIADB_DATABASE=wordpress \
            -e MARIADB_USER=wp -e MARIADB_PASSWORD=wp mariadb:10.11 >/dev/null
    fi

    # Le conteneur WP-CLI tourne avec l'UID hôte pour que les fichiers restent possédés
    # par l'utilisateur actuel (sinon le cp des médias échouerait en permissions).
    # On le recrée toujours : il doit monter LE dossier courant (multi-blogs).
    if command -v id >/dev/null 2>&1; then
        UID_NUM="$(id -u)" ; GID_NUM="$(id -g)"
    fi
    docker rm -f "$NB_CLI" >/dev/null 2>&1 || true
    ok "Démarrage de WP-CLI…"
    if [ -n "$UID_NUM" ]; then
        docker run -d --name "$NB_CLI" --network "$NB_NET" -u "$UID_NUM:$GID_NUM" \
            -e WP_CLI_CACHE_DIR=/tmp/wp-cli \
            -v "$NB_WP:/var/www/html" wordpress:cli sleep infinity >/dev/null
    else
        docker run -d --name "$NB_CLI" --network "$NB_NET" \
            -v "$NB_WP:/var/www/html" wordpress:cli sleep infinity >/dev/null
    fi

    echo -n "  Attente de MariaDB…"
    local i
    for i in $(seq 1 60); do
        if docker exec "$NB_DB" mariadb -uroot -proot -e 'SELECT 1' >/dev/null 2>&1; then
            echo " prête."
            break
        fi
        [ "$i" -eq 60 ] && { echo " échec."; err "MariaDB ne répond pas."; return 1; }
        sleep 1
    done

    # Nouveau dossier (aucun WordPress) : base vierge + installation neuve.
    # Un dossier déjà restauré garde sa base (jamais effacée).
    if [ ! -f "$NB_WP/wp-load.php" ]; then
        ok "Base vierge pour ce blog…"
        docker exec "$NB_DB" mariadb -uroot -proot -e \
            "DROP DATABASE IF EXISTS wordpress; CREATE DATABASE wordpress CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;" >/dev/null 2>&1 || true
        ok "Téléchargement de WordPress…"
        if ! docker exec "$NB_CLI" php -d memory_limit=512M /usr/local/bin/wp core download \
            --path=/var/www/html --locale=fr_FR --allow-root >/dev/null 2>&1; then
            docker exec "$NB_CLI" php -d memory_limit=512M /usr/local/bin/wp core download \
                --path=/var/www/html --allow-root >/dev/null 2>&1 || \
                { err "Échec du téléchargement de WordPress (réseau du conteneur ?)."; return 1; }
        fi
        docker exec "$NB_CLI" php -d memory_limit=512M /usr/local/bin/wp config create \
            --path=/var/www/html --dbname=wordpress --dbuser=wp --dbpass=wp \
            --dbhost="$NB_DB" --allow-root >/dev/null 2>&1 || true
    fi

    # Installation initiale (compte admin, une seule fois)
    if ! docker exec "$NB_CLI" php -d memory_limit=512M /usr/local/bin/wp core is-installed \
        --path=/var/www/html --allow-root >/dev/null 2>&1; then
        local NB_ADMIN_USER="admin" NB_ADMIN_PASS=""
        NB_ADMIN_PASS="$(tr -dc 'a-zA-Z0-9' </dev/urandom 2>/dev/null | head -c 12 || true)"
        [ -n "$NB_ADMIN_PASS" ] || NB_ADMIN_PASS="noblogs2026"
        docker exec "$NB_CLI" php -d memory_limit=512M /usr/local/bin/wp core install \
            --path=/var/www/html --url="http://localhost:$NB_PORT" \
            --title="$(printf '%s\n' "$BACKUP_TITLE — restauration NoBlogs")" \
            --admin_user="$NB_ADMIN_USER" --admin_password="$NB_ADMIN_PASS" \
            --admin_email="admin@example.org" --skip-email --allow-root >/dev/null 2>&1 && \
            echo "  Admin WordPress : $NB_ADMIN_USER / $NB_ADMIN_PASS"
    fi

    # Serveur web (affichage http://localhost:$NB_PORT) — recréé pour monter ce dossier
    if [ -f "$NB_WP/wp-load.php" ] && [ ! -f "$NB_WP/wp-config.php" ]; then
        warn "wp-config.php absent — le serveur web n'affichera pas le site."
    fi
    docker rm -f "$NB_WEB" >/dev/null 2>&1 || true
    ok "Démarrage du serveur web…"
    docker run -d --name "$NB_WEB" --network "$NB_NET" -p "$NB_PORT:80" \
        -v "$NB_WP:/var/www/html" wordpress:latest >/dev/null

    WP="$NB_WP"
    WP_DOCKER="$NB_CLI"
    return 0
}

# --- Mode interactif ---
INTERACTIVE=0
if [ -z "$WP" ] && [ -t 0 ] 2>/dev/null; then
    INTERACTIVE=1
fi

if [ "$INTERACTIVE" = "1" ]; then
    echo ""
    echo -e "${C}╔══════════════════════════════════════════════════════════════╗${N}"
    echo -e "${C}║          Assistant de restauration NoBlogs                  ║${N}"
    echo -e "${C}╚══════════════════════════════════════════════════════════════╝${N}"
    echo ""
    if [ -n "$BACKUP_TITLE" ]; then
        echo -e "  Backup : ${B}$BACKUP_SLUG${N} — $BACKUP_TITLE"
    else
        echo -e "  Backup : ${B}$BACKUP_SLUG${N}"
    fi
    echo ""

    # --- Étape 1 : détecter les installations ---
    echo -e "${C}Détection des installations WordPress…${N}"
    mapfile -t INSTALLS < <(detect_wp | sort -u)

    if [ ${#INSTALLS[@]} -eq 0 ]; then
        warn "Aucune installation WordPress détectée automatiquement."
        echo ""
        echo "  [1]  Indiquer le chemin moi-même"
        echo "  [2]  Auto-installer WordPress avec Docker (recommandé)"
        echo ""
        while true; do
            read -rp "  Votre choix [1/2] : " NOBLOGS_CHOICE
            NOBLOGS_CHOICE="${NOBLOGS_CHOICE:-2}"
            if [ "$NOBLOGS_CHOICE" = "2" ]; then
                if auto_install_docker; then
                    ok "WordPress installé : $WP"
                    break
                fi
                echo ""
                echo "  [1]  Indiquer le chemin moi-même"
                echo ""
                NOBLOGS_CHOICE="1"
            fi
            if [ "$NOBLOGS_CHOICE" = "1" ]; then
                read -rp "  Chemin vers votre WordPress (ex: /var/www/html) : " WP
                WP="${WP/#\~/$HOME}"
                if [ -f "$WP/wp-load.php" ]; then
                    ok "WordPress trouvé : $WP"
                    break
                fi
                err "wp-load.php introuvable dans '$WP'. Réessayez."
            fi
            echo ""
        done
    else
        echo ""
        for i in "${!INSTALLS[@]}"; do
            printf "  ${C}[%d]${N}  %s\n" $((i+1)) "${INSTALLS[$i]}"
        done
        echo -e "  ${C}[+]${N}  Autre chemin (saisie libre)"
        echo ""

        CHOICE=""
        while true; do
            read -rp "  WordPress cible [1-${#INSTALLS[@]} ou +] : " CHOICE
            CHOICE="${CHOICE:-1}"
            if [ "$CHOICE" = "+" ]; then
                while true; do
                    read -rp "  Chemin : " WP
                    WP="${WP/#\~/$HOME}"
                    if [ -f "$WP/wp-load.php" ]; then
                        ok "WordPress trouvé : $WP"
                        break 2
                    fi
                    err "wp-load.php introuvable dans '$WP'."
                done
            elif [[ "$CHOICE" =~ ^[0-9]+$ ]] && [ "$CHOICE" -ge 1 ] && [ "$CHOICE" -le ${#INSTALLS[@]} ]; then
                WP="${INSTALLS[$((CHOICE-1))]}"
                ok "WordPress sélectionné : $WP"
                break
            else
                err "Choix invalide."
            fi
        done
    fi

    setup_wp_cmd

    # --- Étape 2 : URL ---
    echo ""
    CURRENT_SITEURL=""
    if have_wp; then
        CURRENT_SITEURL=$(WPQ option get siteurl 2>/dev/null | tr -d '[:space:]' || true)
    fi

    if [ -n "$BACKUP_ORIGINAL_URL" ]; then
        echo -e "  Blog original : ${Y}$BACKUP_ORIGINAL_URL${N}"
    fi
    if [ -n "$CURRENT_SITEURL" ]; then
        echo -e "  URL actuelle  : ${C}$CURRENT_SITEURL${N}"
    fi
    echo ""
    echo "  Voulez-vous remplacer les anciennes URLs dans les contenus ?"
    if [ -n "$CURRENT_SITEURL" ]; then
        echo "    Entrée = oui, vers $CURRENT_SITEURL"
    fi
    echo "    n = non (conserver les URLs d'origine)"
    echo "    Ou saisissez une URL personnalisée"
    echo ""
    URL_INPUT=""
    read -rp "  > " URL_INPUT
    if [ -z "$URL_INPUT" ]; then
        URL="${CURRENT_SITEURL:-}"
    elif [ "$URL_INPUT" = "n" ] || [ "$URL_INPUT" = "N" ]; then
        URL=""
    else
        URL="$URL_INPUT"
    fi

    if [ -n "$URL" ]; then
        ok "URL cible : $URL"
    else
        ok "Aucun remplacement d'URL"
    fi

    # --- Étape 3 : Résumé ---
    echo ""
    echo -e "${C}╔══════════════════════════════════════════════════════════════╗${N}"
    echo -e "${C}║                     Résumé                                  ║${N}"
    echo -e "${C}╚══════════════════════════════════════════════════════════════╝${N}"
    printf "  %-14s %s\n" "Blog :" "$BACKUP_SLUG"
    [ -n "$BACKUP_TITLE" ] && printf "  %-14s %s\n" "Titre :" "$BACKUP_TITLE"
    printf "  %-14s %d articles, %d pages\n" "Contenu :" "$BACKUP_POSTS" "$BACKUP_PAGES"
    printf "  %-14s %d fichiers\n" "Médias :" "$BACKUP_MEDIA"
    [ -n "$BACKUP_THEME" ] && printf "  %-14s %s\n" "Thème :" "$BACKUP_THEME"
    [ -n "$BACKUP_FIDELITY" ] && printf "  %-14s %s\n" "Fidélité :" "$BACKUP_FIDELITY (widgets, menus, CSS, couleurs)"
    printf "  %-14s %s\n" "WordPress :" "$WP"
    if [ -n "$URL" ]; then
        printf "  %-14s %s\n" "URL cible :" "$URL"
    else
        printf "  %-14s %s\n" "URL cible :" "(inchangée)"
    fi
    echo ""
    CONFIRM=""
    read -rp "  Lancer la restauration ? (O/n) : " CONFIRM
    if [ "$CONFIRM" = "n" ] || [ "$CONFIRM" = "N" ]; then
        echo "  Annulé."
        exit 0
    fi
    echo ""

else
    # --- Mode non-interactif (WP=... requis) ---
    if [ -z "$WP" ]; then
        err "WP non défini. Usage : WP=/var/www/html ./restore.sh"
        exit 1
    fi
    setup_wp_cmd
fi

# --- Validation ---
if [ ! -f "$WP/wp-load.php" ]; then
    err "WordPress non trouvé dans $WP (wp-load.php absent)."
    exit 1
fi

echo -e "${C}══════════════════════════════════════════════════════════════${N}"
echo -e "  ${C}Restauration${N}  ${B}$BACKUP_SLUG${N}  →  $WP"
echo -e "${C}══════════════════════════════════════════════════════════════${N}"
echo ""

# ========================== 1. MEDIAS =======================================
echo -e "${C}1/7  Médias${N}"
if [ -d "$SCRIPT_DIR/uploads" ] && [ "$(ls -A "$SCRIPT_DIR/uploads" 2>/dev/null)" ]; then
    $MKDIR "$WP/wp-content/uploads"
    $CP -rn "$SCRIPT_DIR/uploads/." "$WP/wp-content/uploads/"
    ok "Médias copiés dans wp-content/uploads/"
elif [ -d "$SCRIPT_DIR/fidelity_media" ] && [ "$(ls -A "$SCRIPT_DIR/fidelity_media" 2>/dev/null)" ]; then
    $MKDIR "$WP/wp-content/uploads"
    $CP -rn "$SCRIPT_DIR/fidelity_media/." "$WP/wp-content/uploads/"
    ok "Médias fidélité copiés (backup réduit)"
else
    warn "Aucun média à copier."
fi

# ========================== 2. THEME ========================================
echo -e "${C}2/7  Thème${N}"
if [ -d "$SCRIPT_DIR/theme" ]; then
    THEME_DIR=$(ls -d "$SCRIPT_DIR/theme"/*/ 2>/dev/null | head -1 || true)
    if [ -n "$THEME_DIR" ]; then
        THEME_NAME=$(basename "$THEME_DIR")
        $MKDIR "$WP/wp-content/themes"
        $CP -r "$THEME_DIR" "$WP/wp-content/themes/$THEME_NAME"
        ok "Thème '$THEME_NAME' copié"
        # Thème récupéré sans templates PHP (le blog d'origine ne sert pas les .php,
        # webmaster disparu) → générer un squelette minimal (header/footer/index)
        # pour que le site rende avec le style.css archivé. Fait AVANT l'activation,
        # sinon WordPress refuse d'activer un thème sans index.php.
        if [ ! -f "$WP/wp-content/themes/$THEME_NAME/index.php" ]; then
            cat > "$WP/wp-content/themes/$THEME_NAME/header.php" << 'HEADERPHP'
<?php
/**
 * En-tête minimal de secours — généré par noblogs-backup restore.sh.
 */
?><!DOCTYPE html>
<html <?php language_attributes(); ?>>
<head>
<meta charset="<?php bloginfo( 'charset' ); ?>">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title><?php echo esc_html( wp_get_document_title() ); ?></title>
<link rel="stylesheet" href="<?php echo esc_url( get_stylesheet_uri() ); ?>">
<?php wp_head(); ?>
</head>
<body <?php body_class(); ?>>
<div id="page" class="hfeed site">
    <header id="masthead" class="site-header">
        <h1 class="site-title"><a href="<?php echo esc_url( home_url( '/' ) ); ?>" rel="home"><?php bloginfo( 'name' ); ?></a></h1>
        <?php $desc = get_bloginfo( 'description', 'display' ); ?>
        <?php if ( $desc ) : ?><p class="site-description"><?php echo $desc; ?></p><?php endif; ?>
    </header>
    <div id="content" class="site-content">
HEADERPHP
            cat > "$WP/wp-content/themes/$THEME_NAME/footer.php" << 'FOOTERPHP'
<?php
/**
 * Pied de page minimal de secours — généré par noblogs-backup restore.sh.
 */
?>
    </div><!-- #content -->
    <footer id="colophon" class="site-footer">
        <p><?php bloginfo( 'name' ); ?> — <?php esc_html_e( 'archivé avec NoBlogs Backup', 'minimalism' ); ?></p>
    </footer>
</div><!-- #page -->
<?php wp_footer(); ?>
</body>
</html>
FOOTERPHP
            cat > "$WP/wp-content/themes/$THEME_NAME/index.php" << 'INDEXPHP'
<?php
/**
 * Modèle minimal de secours — généré par noblogs-backup restore.sh.
 */
get_header(); ?>
<main id="main" class="site-main" role="main">
<?php if ( have_posts() ) : ?>
    <?php while ( have_posts() ) : the_post(); ?>
        <article id="post-<?php the_ID(); ?>" <?php post_class(); ?>>
            <header class="entry-header">
                <?php if ( is_singular() ) : ?>
                    <h1 class="entry-title"><?php the_title(); ?></h1>
                <?php else : ?>
                    <h2 class="entry-title"><a href="<?php the_permalink(); ?>" rel="bookmark"><?php the_title(); ?></a></h2>
                <?php endif; ?>
            </header>
            <div class="entry-content">
                <?php the_content(); ?>
            </div>
        </article>
    <?php endwhile; ?>
    <div class="navigation"><p><?php posts_nav_link(); ?></p></div>
<?php else : ?>
    <p><?php esc_html_e( 'Rien à afficher.', 'minimalism' ); ?></p>
<?php endif; ?>
</main>
<?php get_footer(); ?>
INDEXPHP
            ok "Squelette de thème généré (header/footer/index — templates PHP indisponibles)"
        fi
        if have_wp; then
            WPQ theme activate "$THEME_NAME" 2>/dev/null && \
                ok "Thème '$THEME_NAME' activé" || \
                warn "Activation échouée — activez-le manuellement."
        fi
    else
        warn "Aucun dossier thème trouvé."
    fi
else
    warn "Dossier theme/ absent — le thème par défaut restera actif."
fi

# ========================== 3. IMPORT WXR ===================================
echo -e "${C}3/7  Import WXR (articles + pages)${N}"
if [ "$SKIP_WXR" = "1" ]; then
    info "Import WXR ignoré (SKIP_WXR=1)."
elif [ -f "$SCRIPT_DIR/wordpress-export.xml" ]; then
    if have_wp; then
        WPQ plugin is-active wordpress-importer >/dev/null 2>&1 || \
            WPQ plugin install wordpress-importer --activate 2>/dev/null || true
        WPQ import "$SCRIPT_DIR/wordpress-export.xml" --authors=create 2>&1 | tail -5
        ok "Import WXR terminé."
        # Re-pointer la médiathèque vers les fichiers archivés (l'importeur
        # re-télécharge les pièces jointes → doublons "-1") et nettoyer.
        if [ -f "$SCRIPT_DIR/media_relink.php" ]; then
            WPQ eval-file "$SCRIPT_DIR/media_relink.php" 2>&1 | tail -5
            ok "Médiathèque reliée aux fichiers archivés."
        fi
        # L'importeur crée l'auteur en rôle "subscriber" (pas de droit de
        # publication) → les articles animés seraient importés en "draft".
        # On republie les posts/pages dont le slug figure dans le WXR.
        PUB_TMP="$SCRIPT_DIR/.noblogs_publish_$$.php"
        cat > "$PUB_TMP" << 'PUBPHP'
<?php
global $wpdb;
$wxr = $args[0] ?? null;   // passé par restore.sh
if (!$wxr || !is_file($wxr)) { echo "wordpress-export.xml introuvable.\n"; exit; }
$raw = file_get_contents($wxr);
if (!preg_match_all('#<wp:post_name><!\[CDATA\[([^\]]+)\]\]></wp:post_name>#', $raw, $m)) {
    echo "Aucun post_name trouvé.\n"; exit;
}
$p = $wpdb->prefix . "posts";
$n = 0;
foreach (array_unique($m[1]) as $slug_raw) {
    // Normalise : décodage URL + retrait des soft hyphens (U+00AD) que
    // WordPress a éliminés de son côté lors de l'import.
    $slug = rawurldecode($slug_raw);
    $slug = preg_replace('/\x{00AD}/u', '', $slug);
    if (!$slug) continue;
    $res = $wpdb->get_results($wpdb->prepare(
        "SELECT ID FROM {$p} WHERE post_status='draft' AND (post_name=%s OR post_name LIKE %s)",
        $slug, $slug . '-%'
    ));
    foreach ($res as $r) {
        $wpdb->update($p, ['post_status' => 'publish'], ['ID' => $r->ID]);
        $n++;
    }
}
echo "→ Articles republiés : $n\n";
PUBPHP
        if WPQ eval-file "$PUB_TMP" "$SCRIPT_DIR/wordpress-export.xml" 2>&1 | tail -3; then
            ok "Articles republiés (statut importé en draft corrigé)."
        else
            warn "Échec republication."
        fi
        rm -f "$PUB_TMP"
    else
        warn "WP-CLI absent — importez 'wordpress-export.xml' via Outils > Importer > WordPress."
    fi
else
    err "wordpress-export.xml non trouvé."
fi

# ========================== 4. URL REPLACEMENT ==============================
echo -e "${C}4/7  Remplacement des URLs${N}"
if [ -n "$URL" ] && have_wp; then
    ORIGINAL_URL="$BACKUP_ORIGINAL_URL"
    if [ -n "$ORIGINAL_URL" ]; then
        WPQ search-replace "$ORIGINAL_URL" "$URL" --all-tables 2>/dev/null && \
            ok "URLs remplacées : $ORIGINAL_URL → $URL" || \
            warn "Remplacement partiel — vérifiez les URLs."
        WPQ search-replace "${ORIGINAL_URL}/files/" "${URL}/wp-content/uploads/" --all-tables 2>/dev/null || true
        WPQ search-replace "/files/" "/wp-content/uploads/" --all-tables 2>/dev/null || true
    else
        warn "URL d'origine inconnue — pas de remplacement automatique."
    fi
else
    if [ -z "$URL" ]; then
        info "Pas de remplacement d'URL."
    fi
fi

# ========================== 5. PARITY (widgets, menus, CSS) =================
echo -e "${C}5/7  Fidélité visuelle${N}"
if [ -f "$SCRIPT_DIR/fidelity.json" ] && [ -f "$SCRIPT_DIR/restore_parity.php" ] && have_wp; then
    WPQ eval-file "$SCRIPT_DIR/restore_parity.php" 2>&1 | head -20
    ok "Fidélité appliquée."
else
    [ ! -f "$SCRIPT_DIR/fidelity.json" ] && info "fidelity.json absent — pas de fidélité."
    [ ! -f "$SCRIPT_DIR/restore_parity.php" ] && warn "restore_parity.php absent."
fi

# ========================== 6. MORE TAGS ====================================
echo -e "${C}6/7  Balises <!--more-->${N}"
if have_wp; then
    MORE_TMP="$SCRIPT_DIR/.noblogs_more_$$.php"
    cat > "$MORE_TMP" << 'MOREPHP'
<?php
global $wpdb;
$t = $wpdb->prefix . "posts";
$wpdb->query("UPDATE {$t} SET post_content = REGEXP_REPLACE(post_content, '<p><span id=\"more-[0-9]+\"></span></p>', '<!--more-->')");
$wpdb->query("UPDATE {$t} SET post_content = REGEXP_REPLACE(post_content, '<span id=\"more-[0-9]+\"></span>', '<!--more-->')");
$wpdb->query("UPDATE {$t} SET post_content = REGEXP_REPLACE(post_content, '<p><!--more--></p>', '<!--more-->')");
MOREPHP
    WPQ eval-file "$MORE_TMP" 2>/dev/null && \
        ok "Tags <!--more--> restaurés." || \
        warn "Échec restauration more."
    rm -f "$MORE_TMP"
fi

# ========================== 7. FINAL CLEANUP ================================
echo -e "${C}7/7  Nettoyage final${N}"
if have_wp; then
    WPQ rewrite flush 2>/dev/null
    WPQ cache flush 2>/dev/null
    # Supprimer uniquement le contenu par défaut WordPress ("Hello world!"),
    # jamais les vrais articles d'un site existant.
    DEFAULT_IDS=$(WPQ post list --post_type=post,page --post_status=publish,draft \
        --fields=ID,post_title --format=csv 2>/dev/null | while IFS=, read -r id title; do
        case "$title" in
            *"Hello world!"*|*"Sample Page"*|*"Bonjour tout le monde"*|*"Page d"*"exemple"*|*"Politique de confidentialité"*|*"Privacy Policy"*) echo "$id" ;;
        esac
    done | tr '\n' ' ')
    # shellcheck disable=SC2086
    [ -n "${DEFAULT_IDS:-}" ] && WPQ post delete $DEFAULT_IDS --force 2>/dev/null || true
    WPQ option update use_balanceTags 1 2>/dev/null || true
fi

MU_DIR="$WP/wp-content/mu-plugins"
$MKDIR "$MU_DIR" 2>/dev/null || mkdir -p "$MU_DIR"
if [ ! -f "$MU_DIR/force-layout-balance.php" ]; then
    if [ -n "$WP_SUDO" ]; then
        cat > /tmp/_mu_balance_$$.php << 'MUPHP'
<?php
add_filter("the_content", "force_balance_tags", 99);
add_filter("the_excerpt", "force_balance_tags", 99);
MUPHP
        if $CP /tmp/_mu_balance_$$.php "$MU_DIR/force-layout-balance.php" 2>/dev/null; then
            ok "Mu-plugin force-layout-balance installé."
        else
            warn "Impossible d'écrire le mu-plugin (permissions) — écrivez-le manuellement."
        fi
        rm -f /tmp/_mu_balance_$$.php
    else
        cat > "$MU_DIR/force-layout-balance.php" << 'MUPHP'
<?php
add_filter("the_content", "force_balance_tags", 99);
add_filter("the_excerpt", "force_balance_tags", 99);
MUPHP
        ok "Mu-plugin force-layout-balance installé."
    fi
fi

# --- Rapport final ---
FINAL_SITEURL=""
if have_wp; then
    FINAL_SITEURL=$(WPQ option get siteurl 2>/dev/null | tr -d '[:space:]' || true)
fi
FINAL_POSTS=0
if have_wp; then
    FINAL_POSTS=$(WPQ post list --post_type=post --format=count 2>/dev/null || echo "?")
fi
FINAL_PAGES=0
if have_wp; then
    FINAL_PAGES=$(WPQ post list --post_type=page --format=count 2>/dev/null || echo "?")
fi

echo ""
echo -e "${G}══════════════════════════════════════════════════════════════${N}"
echo -e "  ${G}Restauration terminée !${N}"
echo ""
printf "  %-14s %s\n" "Blog :" "$BACKUP_SLUG"
printf "  %-14s %s articles, %s pages\n" "Contenu :" "$FINAL_POSTS" "$FINAL_PAGES"
printf "  %-14s %s\n" "WordPress :" "$WP"
[ -n "$FINAL_SITEURL" ] && printf "  %-14s %s\n" "Site :" "$FINAL_SITEURL"
echo -e "${G}══════════════════════════════════════════════════════════════${N}"
echo ""
