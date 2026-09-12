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

# --- Chargement métadonnées ---
BACKUP_TITLE="" ; BACKUP_SLUG="" ; BACKUP_THEME="" ; BACKUP_POSTS=0 ; BACKUP_PAGES=0
BACKUP_MEDIA=0 ; BACKUP_FIDELITY="" ; BACKUP_ORIGINAL_URL=""

if [ -f "$SCRIPT_DIR/metadata.json" ]; then
    eval "$(python3 -c "
import json, os
d = json.load(open('$SCRIPT_DIR/metadata.json'))
print('BACKUP_SLUG=' + repr(d.get('slug','')))
print('BACKUP_TITLE=' + repr(d.get('title') or ''))
print('BACKUP_THEME=' + repr(d.get('theme') or ''))
print('BACKUP_POSTS=' + str(d.get('posts_count',0)))
print('BACKUP_PAGES=' + str(d.get('pages_count',0)))
print('BACKUP_MEDIA=' + str(d.get('media_success',0)))
print('BACKUP_FIDELITY=' + repr('oui' if d.get('fidelity') else ''))
print('BACKUP_ORIGINAL_URL=' + repr(d.get('original_url','')))
" 2>/dev/null)" || true
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
CP="cp"
MKDIR="mkdir -p"
WPQ() {
    if [ -n "$WP_SUDO" ]; then
        $WP_SUDO wp --path="$WP" --allow-root "$@"
    else
        wp --path="$WP" --allow-root "$@"
    fi
}

setup_wp_cmd() {
    if command -v wp &>/dev/null; then
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
        while true; do
            read -rp "  Chemin vers votre WordPress (ex: /var/www/html) : " WP
            WP="${WP/#\~/$HOME}"
            if [ -f "$WP/wp-load.php" ]; then
                ok "WordPress trouvé : $WP"
                break
            fi
            err "wp-load.php introuvable dans '$WP'. Réessayez."
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
    if command -v wp &>/dev/null; then
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
        if command -v wp &>/dev/null; then
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
    if command -v wp &>/dev/null; then
        WPQ plugin is-active wordpress-importer >/dev/null 2>&1 || \
            WPQ plugin install wordpress-importer --activate 2>/dev/null || true
        WPQ import "$SCRIPT_DIR/wordpress-export.xml" --authors=create
        ok "Import WXR terminé."
    else
        warn "WP-CLI absent — importez 'wordpress-export.xml' via Outils > Importer > WordPress."
    fi
else
    err "wordpress-export.xml non trouvé."
fi

# ========================== 4. URL REPLACEMENT ==============================
echo -e "${C}4/7  Remplacement des URLs${N}"
if [ -n "$URL" ] && command -v wp &>/dev/null; then
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
if [ -f "$SCRIPT_DIR/fidelity.json" ] && [ -f "$SCRIPT_DIR/restore_parity.php" ] && command -v wp &>/dev/null; then
    WPQ eval-file "$SCRIPT_DIR/restore_parity.php" 2>&1 | head -20
    ok "Fidélité appliquée."
else
    [ ! -f "$SCRIPT_DIR/fidelity.json" ] && info "fidelity.json absent — pas de fidélité."
    [ ! -f "$SCRIPT_DIR/restore_parity.php" ] && warn "restore_parity.php absent."
fi

# ========================== 6. MORE TAGS ====================================
echo -e "${C}6/7  Balises <!--more-->${N}"
if command -v wp &>/dev/null; then
    cat > /tmp/_restore_more_$$.sql << 'SQL'
UPDATE wp_posts SET post_content = REGEXP_REPLACE(post_content, '<p><span id="more-[0-9]+"></span></p>', '<!--more-->');
UPDATE wp_posts SET post_content = REGEXP_REPLACE(post_content, '<span id="more-[0-9]+"></span>', '<!--more-->');
UPDATE wp_posts SET post_content = REGEXP_REPLACE(post_content, '<p><!--more--></p>', '<!--more-->');
SQL
    WPQ db query < /tmp/_restore_more_$$.sql 2>/dev/null && \
        ok "Tags <!--more--> restaurés." || \
        warn "Échec restauration more."
    rm -f /tmp/_restore_more_$$.sql
fi

# ========================== 7. FINAL CLEANUP ================================
echo -e "${C}7/7  Nettoyage final${N}"
if command -v wp &>/dev/null; then
    WPQ rewrite flush 2>/dev/null
    WPQ cache flush 2>/dev/null
    # Supprimer uniquement le contenu par défaut WordPress ("Hello world!"),
    # jamais les vrais articles d'un site existant.
    WPQ post delete $(WPQ post list --post_type=post,page --post_status=publish,draft --field=ID --format=ids 2>/dev/null | while read -r id; do
        t=$(WPQ post get "$id" --field=post_title 2>/dev/null | tr '\302\240 ' '  ' || true)
        case "$t" in
            "Hello world!"*|"Sample Page"*|"Bonjour tout le monde !"*|"Page d'exemple"*|"Politique de confidentialité"*) echo "$id" ;;
        esac
    done | tr '\n' ' ') --force 2>/dev/null || true
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
if command -v wp &>/dev/null; then
    FINAL_SITEURL=$(WPQ option get siteurl 2>/dev/null | tr -d '[:space:]' || true)
fi
FINAL_POSTS=0
if command -v wp &>/dev/null; then
    FINAL_POSTS=$(WPQ post list --post_type=post --format=count 2>/dev/null || echo "?")
fi
FINAL_PAGES=0
if command -v wp &>/dev/null; then
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
