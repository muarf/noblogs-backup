<?php
/**
 * media_relink.php — Réassocie la médiathèque WordPress aux fichiers archivés.
 *
 * Exécuté par restore.sh via `wp eval-file`, APRÈS l'import WXR.
 *
 * Contexte : l'importeur WordPress re-télécharge chaque pièce jointe depuis son
 * URL distante et la range sous uploads/AAAA/MM (souvent doublons "-1", mauvais
 * dossier) ; si le blog d'origine est mort, le téléchargement échoue même et la
 * médiathèque pointe vers des fichiers absents.
 *
 * Ce script re-pointe chaque attachment vers le fichier réellement archivé dans
 * le backup (uploads/ du même dossier) et supprime les doublons re-téléchargés.
 *
 * ATTENTION : exécuter avec WP-CLI, jamais en HTTP.
 */
if (php_sapi_name() !== 'cli') {
    exit("Ce script doit être exécuté via WP-CLI : wp eval-file media_relink.php\n");
}

$dir        = __DIR__;
$backup_uploads = "$dir/uploads";

if (!is_dir($backup_uploads)) {
    echo "Dossier uploads du backup absent ($backup_uploads).\n";
    echo "Le backup a été restauré sans médias archivés — rien à faire.\n";
    exit;
}

$uploads = wp_upload_dir();
$basedir = $uploads['basedir'];

// ------------------------------------------------------------ INDEX BACKUP
$src_files = [];          // basename → rel (chemin relatif dans backup upluploads)
$src_lookup = [];         // "canon" (basename sans taille ni -N) → rel
foreach (new RecursiveIteratorIterator(
    new RecursiveDirectoryIterator($backup_uploads, FilesystemIterator::SKIP_DOTS)
) as $f) {
    if (!$f->isFile()) continue;
    $rel = ltrim(str_replace('\\', '/', str_replace($backup_uploads, '', $f->getPathname())), '/');
    $bn  = basename($rel);
    $src_files[$bn][] = $rel;
    $src_lookup[canonical_name($bn)][] = $rel;
}

function canonical_name(string $bn): string {
    // retire suffixe de taille ("-150x150") puis un éventuel "-N" ajouté par l'importeur
    $bn = preg_replace('/-\d{1,4}x\d{1,4}(?=\.)/', '', $bn);
    $bn = preg_replace('/-\d$(?=\.)/', '', $bn);
    return $bn;
}

function strip_dup_suffix(string $bn): string {
    // "-150x150-1.jpg" → recherche de la taille de base, retire "-1"
    return preg_replace('/-(\d{1,4}x\d{1,4}|)-\d+(?=\.)$/', '-$1', $bn);
}

// ------------------------------------------------------------ RELINK
global $wpdb;
$rows = $wpdb->get_results(
    "SELECT ID FROM {$wpdb->posts} WHERE post_type = 'attachment'"
);

$relinked = 0;
$orphans  = [];   // anciens fichiers pointés (doublons/absents) → rel
foreach ($rows as $row) {
    $id  = (int) $row->ID;
    $rel = get_post_meta($id, '_wp_attached_file', true);
    if (!$rel) continue;

    $bn = basename($rel);
    $target = null;

    // 1) fichier du backup strictement identique
    foreach ($src_files[$bn] ?? [] as $cand) {
        $target = $cand;
        break;
    }

    // 2) même canon mais avec doublon "-1" ou taille/année différente
    if (!$target) {
        foreach ($src_lookup[canonical_name($bn)] ?? [] as $cand) {
            $target = $cand;
            break;
        }
    }
    // 3) basename sans son suffixe "-N" de doublon => recherche inverse
    $base_no_dup = strip_dup_suffix($bn);
    if (!$target && $base_no_dup !== $bn) {
        foreach ($src_lookup[canonical_name($base_no_dup)] ?? [] as $cand) {
            $target = $cand;
            break;
        }
    }

    if (!$target) {
        $orphans[$rel] = true;    // aucun équivalent archivé : à nettoyer si absent
        continue;
    }

    $target_full = trailingslashit($basedir) . $target;
    if (!is_file($target_full)) {
        $src_full = trailingslashit($backup_uploads) . $target;
        if (is_file($src_full)) {
            wp_mkdir_p(dirname($target_full));
            @copy($src_full, $target_full);
        }
    }
    if ($target !== $rel) {
        update_post_meta($id, '_wp_attached_file', $target);
        $relinked++;
    }
    if ($rel !== $target) {
        $orphans[$rel] = true;
    }
}

// ------------------------------------------------------------ CLEANUP
$removed = 0;
foreach (array_keys($orphans) as $rel) {
    $full = trailingslashit($basedir) . $rel;
    if (!is_file($full)) continue;
    $bn = basename($rel);
    // Fichier réellement archivé (== présent dans le backup uploads) : jamais supprimé.
    // Doublon re-téléchargé : basename exact déjà archivé, OU nom canonique
    // (taille/`-N` retiré) présent dans le backup alors que le fichier lui-même
    // ne l'est pas (importeur → uploads/AAAA/MM/NOM-1.ext).
    $is_archived_exact = isset($src_files[$bn]);
    $is_archived_canon = isset($src_lookup[canonical_name($bn)]);
    $is_dup = $is_archived_canon && !$is_archived_exact;
    if ($is_archived_exact) {
        // Présent dans le backup sous un autre dossier (ex. re-fiché par l'importeur)
        // et sque non archivé à ce chemin exact : doublon → supprimer.
        $rel_in_backup = in_array($rel, $src_files[$bn] ?? [], true);
        if (!$rel_in_backup) $is_dup = true;
    }
    if ($is_dup) {
        @unlink($full);
        $removed++;
    }
}

// vide les dossiers vides restants (ex : uploads/2022/01/ après nettoyage)
$dirs = new RecursiveIteratorIterator(
    new RecursiveDirectoryIterator($basedir, FilesystemIterator::SKIP_DOTS),
    RecursiveIteratorIterator::CHILD_FIRST
);
foreach ($dirs as $d) {
    if ($d->isDir() && !(new FilesystemIterator($d->getPathname()))->valid()) {
        @rmdir($d->getPathname());
    }
}

echo "→ Médiathèque : $relinked fichiers re-pointés vers l'archive, $removed doublons supprimés.\n";

// ------------------------------------------------------------ DOUBLONS PHYSIQUES (md5)
// Passe finale : tout fichier présENT sous wp-content/uploads qui est identique
// (md5) à un fichier archivé mais situé à un autre chemin est une copie faite par
// l'importeur (uploads/AAAA/MM/NOM-1.ext) → suppression.
$archived_md5 = [];   // md5 → rel archivé
foreach (new RecursiveIteratorIterator(
    new RecursiveDirectoryIterator($backup_uploads, FilesystemIterator::SKIP_DOTS)
) as $f) {
    if (!$f->isFile()) continue;
    $rel = ltrim(str_replace('\\', '/', str_replace($backup_uploads, '', $f->getPathname())), '/');
    $archived_md5[md5_file($f->getPathname())] = $rel;
}

$physical_dup = 0;
foreach (new RecursiveIteratorIterator(
    new RecursiveDirectoryIterator($basedir, FilesystemIterator::SKIP_DOTS)
) as $f) {
    if (!$f->isFile()) continue;
    $rel = ltrim(str_replace('\\', '/', str_replace($basedir, '', $f->getPathname())), '/');
    if (isset($archived_md5[md5_file($f->getPathname())]) && $archived_md5[md5_file($f->getPathname())] !== $rel) {
        @unlink($f->getPathname());
        $physical_dup++;
    }
}

// re-vide les dossiers vides en profondeur
$dirs = new RecursiveIteratorIterator(
    new RecursiveDirectoryIterator($basedir, FilesystemIterator::SKIP_DOTS),
    RecursiveIteratorIterator::CHILD_FIRST
);
foreach ($dirs as $d) {
    if ($d->isDir() && !(new FilesystemIterator($d->getPathname()))->valid()) {
        @rmdir($d->getPathname());
    }
}

if ($physical_dup > 0) {
    echo "→ Doublons physiques supprimés (copies d'importeur identiques au backup) : $physical_dup.\n";
}