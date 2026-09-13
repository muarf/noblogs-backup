<?php
/**
 * restore_parity.php — Application de la fidélité visuelle d'un backup NoBlogs.
 *
 * Exécuté par restore.sh via `wp eval-file`. Lit fidelity.json (dans le même
 * dossier), puis :
 *   1. Active le thème et applique header_image / logo / background / couleurs
 *   2. Injecte le Custom CSS (wp_update_custom_css_post)
 *   3. Recrée les widgets de sidebar
 *   4. Recrée le menu de navigation (avec sous-menus)
 *
 * ATTENTION : exécuter avec WP-CLI, jamais en HTTP.
 */
if (php_sapi_name() !== 'cli') {
    exit("Ce script doit être exécuté via WP-CLI : wp eval-file restore_parity.php\n");
}

$dir = __DIR__;
$fid_path = "$dir/fidelity.json";
if (!file_exists($fid_path)) {
    echo "fidelity.json introuvable, rien à faire.\n";
    exit;
}
$fid = json_decode(file_get_contents($fid_path), true);
if (!is_array($fid)) {
    echo "fidelity.json invalide.\n";
    exit;
}

$uploads_url = wp_upload_dir();
$uploads_base = $uploads_url['baseurl'];

function local_uploads_url($url, $uploads_base) {
    if (!$url) return $url;
    // Déjà local ?
    if (strpos($url, $uploads_base) === 0) return $url;
    // Chemin "/files/<année>/<mois>/..." sur le domaine d'origine → uploads/<chemin>
    $path = parse_url($url, PHP_URL_PATH);
    if ($path && preg_match('#/files/(.+)$#', $path, $m)) {
        return trailingslashit($uploads_base) . $m[1];
    }
    $base = basename(parse_url($uploads_base, PHP_URL_PATH));
    if (strpos($url, "$base/") === 0) {
        return trailingslashit($uploads_base) . substr($url, strlen($base) + 1);
    }
    if (strpos($url, "uploads/") === 0) {
        return trailingslashit($uploads_base) . substr($url, strlen("uploads/"));
    }
    return $url;
}

echo "→ Thème : " . ($fid['theme'] ?? '?') . "\n";

// -------------------------------------------------------------- THEME MODS
$theme       = $fid['theme'] ?? get_template();
$serial_theme = preg_replace('/[^a-zA-Z0-9_\-]/', '_', $theme);

// Activer le thème fidélité s'il est présent et différent de l'actif
if (wp_get_theme($theme)->exists() && wp_get_theme()->get_stylesheet() !== $theme) {
    $switched = switch_theme($theme);
    if (is_wp_error($switched)) {
        echo "→ Thème non activé : " . $switched->get_error_message() . "\n";
    } else {
        echo "→ Thème activé : $theme\n";
    }
}

$mods        = get_option("theme_mods_$serial_theme", []);
if (!is_array($mods)) $mods = [];

if (isset($fid['header_image']['url']) && $fid['header_image']['url']) {
    $hj = local_uploads_url($fid['header_image']['url'], $uploads_base);
    $mods['header_image'] = $hj;
    $mods['header_image_data'] = [
        'url'           => $hj,
        'thumbnail_url' => $hj,
        'width'         => $fid['header_image']['width'] ?? 940,
        'height'        => $fid['header_image']['height'] ?? 198,
    ];
}

if (isset($fid['logo']['url']) && $fid['logo']['url']) {
    $mods['custom_logo'] = (int) get_theme_mod('custom_logo');
}

$bg = $fid['background'] ?? [];
if (!empty($bg['background_color'])) {
    $mods['background_color'] = $bg['background_color'];
}
$bg_url = $bg['background_image'] ?? $bg['background_image_local'] ?? '';
if ($bg_url) {
    $mods['background_image']       = local_uploads_url($bg_url, $uploads_base);
    $mods['background_repeat']      = $bg['repeat'] ?? 'repeat';
    $mods['background_position_x']  = $bg['position'] ?? 'left';
    $mods['background_attachment']  = $bg['attachment'] ?? 'fixed';
    $mods['background_size']        = $bg['size'] ?? 'auto';
}

foreach (($fid['theme_colors'] ?? []) as $k => $v) {
    $mods[$k] = $v;
}
if (!empty($fid['link_color'])) {
    $mods['link_color'] = $fid['link_color'];
}
if (!empty($fid['tagline'])) {
    $mods['blogdescription'] = $fid['tagline'];
}

update_option("theme_mods_$serial_theme", $mods);
echo "→ theme_mods appliqués (header, background, couleurs, tagline)\n";

// -------------------------------------------------------------- CUSTOM CSS
if (!empty($fid['custom_css'])) {
    $css = $fid['custom_css'];
    $css = str_replace('uploads/', trailingslashit($uploads_base), $css);
    $css .= "\n" . "#branding img, .header-image img, .custom-header img { max-width: 100% !important; height: auto !important; }";
    if (function_exists('wp_update_custom_css_post')) {
        wp_update_custom_css_post($css);
        echo "→ Custom CSS injecté\n";
    }
}

// -------------------------------------------------------------- TAGLINE
$tagline = $fid['tagline'] ?? '';
if ($tagline) {
    update_option('blogdescription', $tagline);
}

// -------------------------------------------------------------- BLOGNAME
$blogname = $fid['title'] ?? '';
if ($blogname) {
    $old = get_option('blogname');
    if ($old !== $blogname) {
        update_option('blogname', $blogname);
        echo "→ Titre du site restauré : $blogname\n";
    }
}

// -------------------------------------------------------------- SIDEBARS / WIDGETS
$sidebars_map = [];
$storage = [];
$counts = [
    'search' => 2, 'categories' => 2, 'archives' => 2,
    'recent-posts' => 2, 'media_video' => 2, 'custom_html' => 2,
];
foreach (($fid['sidebars'] ?? []) as $sb_id => $widgets) {
    $sidebars_map[$sb_id] = [];
    if (!is_array($widgets)) continue;
    foreach ($widgets as $w) {
        $type = $w['type'] ?? 'custom_html';
        $idx  = $counts[$type] ?? 2;
        $counts[$type] = $idx + 1;
        $key  = "{$type}-{$idx}";
        $sidebars_map[$sb_id][] = $key;
        switch ($type) {
            case 'search':
                $storage['widget_search'][$idx] = ['title' => $w['title'] ?? ''];
                break;
            case 'categories':
                $storage['widget_categories'][$idx] = [
                    'title' => $w['title'] ?? 'Catégories', 'count' => 0,
                    'hierarchical' => 1, 'dropdown' => 0,
                ];
                break;
            case 'archives':
                $storage['widget_archives'][$idx] = [
                    'title' => $w['title'] ?? 'Archives', 'count' => 0, 'dropdown' => 0,
                ];
                break;
            case 'recent-posts':
                $storage['widget_recent-posts'][$idx] = [
                    'title' => $w['title'] ?? 'Articles récents', 'number' => 5, 'show_date' => false,
                ];
                break;
            case 'media_video':
                $storage['widget_media_video'][$idx] = [
                    'title' => $w['title'] ?? '', 'url' => $w['url'] ?? '',
                ];
                break;
            default: // custom_html
                $content = $w['content'] ?? '';
                $content = str_replace('uploads/', trailingslashit($uploads_base), $content);
                $storage['widget_custom_html'][$idx] = [
                    'title' => $w['title'] ?? '', 'content' => $content,
                ];
                break;
        }
    }
}

$registered = [];
foreach (wp_get_sidebars_widgets() as $id => $unused) {
    if ($id === 'wp_inactive_widgets') continue;
    $registered[] = $id;
}
if (empty($registered)) $registered = ['sidebar-1'];
echo "→ Sidebars enregistrées : " . implode(', ', $registered) . "\n";

$map = [];
foreach ($registered as $k => $sb_id) {
    $map[$sb_id] = $sidebars_map[$sb_id] ?? [];
}

foreach ($storage as $opt_key => $instances) {
    if (empty($instances)) continue;
    $current = get_option($opt_key, []);
    if (!is_array($current)) $current = [];
    foreach ($instances as $k => $v) {
        $current[$k] = $v;
    }
    $current['_multiwidget'] = 1;
    update_option($opt_key, $current);
    echo "→ Widgets {$opt_key} : " . count($instances) . " restaurés\n";
}

// Récupérer les widgets actuellement assignés et fusionner
$current_sidebars = wp_get_sidebars_widgets();
foreach ($map as $sb_id => $widget_list) {
    if (!empty($widget_list)) {
        $current_sidebars[$sb_id] = $widget_list;
    }
}
wp_set_sidebars_widgets($current_sidebars);

// -------------------------------------------------------------- MENU
$menu_items = $fid['menu_items'] ?? [];
if (!empty($menu_items)) {
    $existing = wp_get_nav_menus();
    foreach ($existing as $menu) {
        if ($menu->name === 'Menu Principal') {
            wp_delete_nav_menu($menu->term_id);
            break;
        }
    }
    $menu_id = wp_create_nav_menu('Menu Principal');
    if (is_wp_error($menu_id)) {
        echo "→ Menu impossible à créer : " . $menu_id->get_error_message() . "\n";
    } else {
        $locations = get_registered_nav_menus();
        $loc = array_keys($locations);
        $target_loc = in_array('primary', $loc) ? 'primary' : ($loc[0] ?? 'primary');
        foreach ($menu_items as $item) {
            $href = $item['href'] ?? '';
            $href = str_replace('uploads/', trailingslashit($uploads_base), $href);
            $menu_url = (strpos($href, '/') === 0) ? home_url($href) : $href;
            $pid = wp_update_nav_menu_item($menu_id, 0, [
                'menu-item-title'     => $item['title'] ?? '',
                'menu-item-url'       => $menu_url,
                'menu-item-status'    => 'publish',
            ]);
            if (is_wp_error($pid)) continue;
            foreach (($item['children'] ?? []) as $child) {
                $chref = $child['href'] ?? '';
                $chref = str_replace('uploads/', trailingslashit($uploads_base), $chref);
                $menu_child_url = (strpos($chref, '/') === 0) ? home_url($chref) : $chref;
                wp_update_nav_menu_item($menu_id, 0, [
                    'menu-item-title'     => $child['title'] ?? '',
                    'menu-item-url'       => $menu_child_url,
                    'menu-item-status'    => 'publish',
                    'menu-item-parent-id' => $pid,
                ]);
            }
        }
        $theme_mods_menu = get_theme_mod('nav_menu_locations', []);
        $theme_mods_menu[$target_loc] = $menu_id;
        set_theme_mod('nav_menu_locations', $theme_mods_menu);
        echo "→ Menu 'Menu Principal' recréé (" . count($menu_items) . " racines)\n";
    }
}

echo "→ Parité visuelle appliquée.\n";