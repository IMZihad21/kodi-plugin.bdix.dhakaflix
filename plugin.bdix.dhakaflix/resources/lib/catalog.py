"""Curated DhakaFlix category paths and search-node configuration."""

from urllib.parse import urljoin

from resources.lib.urls import normalize_http_url


NODES = {
    "7": {
        "setting": "server_7_url",
        "server_name": "DHAKA-FLIX-7",
        "search_types": ("movie",),
    },
    "9": {
        "setting": "server_9_url",
        "server_name": "DHAKA-FLIX-9",
        "search_types": ("series",),
    },
    "12": {
        "setting": "server_12_url",
        "server_name": "DHAKA-FLIX-12",
        "search_types": ("series",),
    },
    "14": {
        "setting": "server_14_url",
        "server_name": "DHAKA-FLIX-14",
        "search_types": ("movie", "series"),
    },
}

_CATEGORIES = {
    "movie": (
        ("English Movies (720p)", "7", "DHAKA-FLIX-7/English Movies/"),
        ("English Movies (1080p)", "14", "DHAKA-FLIX-14/English Movies (1080p)/"),
        ("Hindi Movies", "14", "DHAKA-FLIX-14/Hindi Movies/"),
        ("South Indian Movies", "14", "DHAKA-FLIX-14/SOUTH INDIAN MOVIES/South Movies/"),
        ("South Indian Hindi Dubbed", "14", "DHAKA-FLIX-14/SOUTH INDIAN MOVIES/Hindi Dubbed/"),
        ("West Bengal Bangla Movies", "7", "DHAKA-FLIX-7/Kolkata Bangla Movies/"),
        ("Animation Movies", "14", "DHAKA-FLIX-14/Animation Movies/"),
        ("Animation Movies (1080p)", "14", "DHAKA-FLIX-14/Animation Movies (1080p)/"),
        ("Foreign Language Movies", "7", "DHAKA-FLIX-7/Foreign Language Movies/"),
        ("IMDb Top 250 Movies", "14", "DHAKA-FLIX-14/IMDb Top-250 Movies/"),
        ("3D Movies", "7", "DHAKA-FLIX-7/3D Movies/"),
    ),
    "series": (
        ("TV & Web Series", "12", "DHAKA-FLIX-12/TV-WEB-Series/"),
        ("Korean TV & Web Series", "14", "DHAKA-FLIX-14/KOREAN TV & WEB Series/"),
        ("Anime & Cartoon Series", "9", "DHAKA-FLIX-9/Anime & Cartoon TV Series/"),
        ("Documentary", "9", "DHAKA-FLIX-9/Documentary/"),
        ("WWE Wrestling", "9", "DHAKA-FLIX-9/WWE & AEW Wrestling/WWE Wrestling/"),
        ("AEW Wrestling", "9", "DHAKA-FLIX-9/WWE & AEW Wrestling/AEW Wrestling/"),
        ("Awards & TV Shows", "9", "DHAKA-FLIX-9/Awards & TV Shows/"),
    ),
}


def categories(media_type, node_urls):
    """Return display labels and validated URLs for configured source nodes."""
    if media_type not in _CATEGORIES:
        raise ValueError("Unsupported category type")
    result = []
    for label, node_id, path in _CATEGORIES[media_type]:
        base_url = node_urls.get(node_id)
        if not base_url:
            continue
        base_url = normalize_http_url(base_url)
        result.append((label, normalize_http_url(urljoin(base_url.rstrip("/") + "/", path))))
    return result


def search_nodes(media_type, node_urls):
    """Return configured nodes that support the requested search type."""
    if media_type not in ("movie", "series"):
        raise ValueError("Unsupported search type")
    configured = []
    for node_id, node in NODES.items():
        if media_type in node["search_types"] and node_urls.get(node_id):
            configured.append(
                (node_id, node["server_name"], normalize_http_url(node_urls[node_id]))
            )
    return configured
