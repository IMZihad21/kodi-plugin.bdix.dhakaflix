#!/usr/bin/env python
"""Kodi plugin entry point for DhakaFlix."""

import sys
import traceback
from concurrent.futures import ThreadPoolExecutor, as_completed
from urllib.parse import unquote, urlsplit

import xbmc
import xbmcaddon
import xbmcgui
import xbmcplugin
import xbmcvfs

from resources.lib.cache import ListingCache
from resources.lib.catalog import NODES, categories, search_nodes
from resources.lib.portal import PortalClient, PortalError
from resources.lib.router import RouteError, parse_route, plugin_url

ADDON_ID = "plugin.bdix.dhakaflix"
ADDON = xbmcaddon.Addon(id=ADDON_ID)
HANDLE = int(sys.argv[1])
BASE_URL = sys.argv[0]


def _log(message, level=xbmc.LOGINFO):
    xbmc.log("[{}] {}".format(ADDON_ID, message), level)


def _notify(message):
    xbmcgui.Dialog().notification(ADDON.getAddonInfo("name"), message)


def _settings():
    origin_url = ADDON.getSetting("origin_url").strip()
    if not origin_url:
        origin_url = "http://172.16.50.7/"
    excluded_keywords = [
        word.strip().casefold()
        for word in ADDON.getSetting("excluded_keywords").split(",")
        if word.strip()
    ]
    try:
        cache_ttl = int(ADDON.getSetting("cache_ttl") or "7200")
    except ValueError:
        cache_ttl = 7200
    try:
        timeout = int(ADDON.getSetting("request_timeout") or "20")
    except ValueError:
        timeout = 20
    node_urls = {}
    for node_id, node in NODES.items():
        node_urls[node_id] = ADDON.getSetting(node["setting"]).strip()
    return (
        origin_url,
        excluded_keywords,
        max(0, cache_ttl),
        min(max(timeout, 3), 60),
        node_urls,
    )


def _cache(ttl_seconds):
    profile = xbmcvfs.translatePath(ADDON.getAddonInfo("profile"))
    return ListingCache(profile, ttl_seconds=ttl_seconds)


def _route_url(action, target_url=None, media_type=None):
    return plugin_url(
        BASE_URL,
        action,
        target_url,
        media_type=media_type,
    )


def _list_item(entry, artwork_url=None):
    item = xbmcgui.ListItem(label=entry.name, path=entry.url)
    if artwork_url:
        item.setArt({"thumb": artwork_url, "icon": artwork_url})
    item.setInfo("video", {"title": entry.name})
    if entry.kind == "video":
        item.setProperty("IsPlayable", "true")
    return item


def _refresh_menu(target_url):
    command = "RunPlugin({})".format(_route_url("refresh", target_url))
    return [("Refresh listing", command)]


def _finish_directory(items, content="videos", succeeded=True):
    xbmcplugin.setContent(HANDLE, content)
    for route, item, is_folder in items:
        xbmcplugin.addDirectoryItem(
            handle=HANDLE,
            url=route,
            listitem=item,
            isFolder=is_folder,
        )
    xbmcplugin.endOfDirectory(
        HANDLE,
        succeeded=succeeded,
        updateListing=False,
        cacheToDisc=False,
    )


def _render_entries(target_url, entries):
    visible = [entry for entry in entries if entry.kind in ("folder", "video")]
    images = [entry for entry in entries if entry.kind == "image"]
    artwork = _select_artwork(images)
    subtitles = [entry for entry in entries if entry.kind == "subtitle"]
    items = []
    for entry in visible:
        if entry.kind == "folder":
            item = _list_item(entry, artwork)
            item.addContextMenuItems(_refresh_menu(target_url))
            items.append((_route_url("browse", entry.url), item, True))
        else:
            item = _list_item(entry, artwork)
            matches = _matching_subtitles(entry.name, subtitles)
            if matches:
                item.setSubtitles([subtitle.url for subtitle in matches])
            item.addContextMenuItems(_refresh_menu(target_url))
            items.append((_route_url("play", entry.url), item, False))

    _finish_directory(items)
    if not visible:
        _notify("No video or folder entries found")


def _select_artwork(images):
    if not images:
        return None
    preferred = ("poster", "cover", "folder", "thumb", "front")
    for keyword in preferred:
        for image in images:
            if keyword in image.name.casefold():
                return image.url
    return images[0].url


def _matching_subtitles(video_name, subtitles):
    video_stem = video_name.rsplit(".", 1)[0].casefold()
    matches = []
    for subtitle in subtitles:
        subtitle_stem = subtitle.name.rsplit(".", 1)[0].casefold()
        if subtitle_stem == video_stem or subtitle_stem.startswith(video_stem + "."):
            matches.append(subtitle)
    return matches


def _browse(client, cache, target_url, refresh=False):
    entries = client.listing(target_url, cache, refresh=refresh)
    _render_entries(target_url, entries)


def _menu_item(label, route, is_folder=True):
    item = xbmcgui.ListItem(label=label)
    item.setInfo("video", {"title": label})
    return route, item, is_folder


def _render_home(origin_url):
    items = [
        _menu_item("Browse configured portal", _route_url("browse", origin_url)),
        _menu_item("Movie categories", _route_url("catalog", media_type="movie")),
        _menu_item("TV & series categories", _route_url("catalog", media_type="series")),
        _menu_item("Search movies", _route_url("search", media_type="movie")),
        _menu_item("Search TV & series", _route_url("search", media_type="series")),
        _menu_item("Add-on settings", _route_url("settings"), is_folder=False),
    ]
    _finish_directory(items, content="videos")


def _render_catalog(media_type, node_urls):
    items = [
        _menu_item(label, _route_url("browse", target_url))
        for label, target_url in categories(media_type, node_urls)
    ]
    _finish_directory(items, content="videos")
    if not items:
        _notify("No source nodes are configured")


def _search_dialog(media_type, client, node_urls):
    heading = "movies" if media_type == "movie" else "TV & series"
    keyboard = xbmc.Keyboard("", "Search {}".format(heading))
    keyboard.doModal()
    if not keyboard.isConfirmed():
        xbmcplugin.endOfDirectory(HANDLE, succeeded=True, cacheToDisc=False)
        return
    query = keyboard.getText().strip()
    if not query:
        xbmcplugin.endOfDirectory(HANDLE, succeeded=True, cacheToDisc=False)
        return
    _search(client, media_type, query, node_urls)


def _search(client, media_type, query, node_urls):
    nodes = search_nodes(media_type, node_urls)
    if not nodes:
        _finish_directory([])
        _notify("No source nodes are configured for this search")
        return

    results = []
    errors = []
    with ThreadPoolExecutor(max_workers=len(nodes)) as executor:
        futures = {
            executor.submit(client.search_videos, base_url, server_name, query): node_id
            for node_id, server_name, base_url in nodes
        }
        for future in as_completed(futures):
            node_id = futures[future]
            try:
                results.extend((node_id, entry) for entry in future.result())
            except PortalError as error:
                errors.append("Node {}: {}".format(node_id, error))
                _log(errors[-1], xbmc.LOGWARNING)

    unique = {}
    for node_id, entry in results:
        unique.setdefault(entry.url, (node_id, entry))
    items = []
    for node_id, entry in sorted(
        unique.values(),
        key=lambda result: (result[1].name.casefold(), result[0]),
    ):
        item = _list_item(entry)
        item.setInfo("video", {
            "title": entry.name,
            "mediatype": "video",
            "studio": "DhakaFlix node {}".format(node_id),
        })
        items.append((_route_url("play", entry.url), item, False))

    _finish_directory(items)
    if errors:
        if items:
            _notify("Some search sources could not be reached")
        else:
            _notify("Search failed: {}".format(errors[0]))
    elif not items:
        _notify("No matching videos found")


def _play(target_url):
    item = xbmcgui.ListItem(path=target_url)
    item.setPath(target_url)
    title = unquote(urlsplit(target_url).path.rsplit("/", 1)[-1])
    item.setInfo("video", {"title": title})
    item.setProperty("IsPlayable", "true")
    xbmcplugin.setResolvedUrl(HANDLE, True, item)


def main():
    try:
        route = parse_route(BASE_URL, sys.argv[2] if len(sys.argv) > 2 else "")
        origin_url, exclusions, cache_ttl, timeout, node_urls = _settings()
        client = PortalClient(
            origin_url=origin_url,
            excluded_keywords=exclusions,
            timeout=timeout,
            user_agent="Kodi DhakaFlix plugin/{}".format(ADDON.getAddonInfo("version")),
            log_warning=lambda message: _log(message, xbmc.LOGWARNING),
        )
        cache = _cache(cache_ttl)

        if route.action == "root":
            _render_home(origin_url)
        elif route.action == "settings":
            ADDON.openSettings()
            xbmcplugin.endOfDirectory(HANDLE, succeeded=True, cacheToDisc=False)
        elif route.action == "play":
            _play(route.target_url)
        elif route.action == "catalog":
            _render_catalog(route.media_type, node_urls)
        elif route.action == "search":
            _search_dialog(route.media_type, client, node_urls)
        elif route.action in ("browse", "refresh"):
            target_url = route.target_url or origin_url
            _browse(client, cache, target_url, refresh=route.action == "refresh")
        else:
            raise RouteError("Unsupported action")
    except (PortalError, RouteError, ValueError) as error:
        _log(str(error), xbmc.LOGERROR)
        xbmcgui.Dialog().ok(ADDON.getAddonInfo("name"), str(error))
        xbmcplugin.endOfDirectory(HANDLE, succeeded=False, cacheToDisc=False)
    except Exception as error:
        _log(
            "Unexpected add-on error: {}\n{}".format(error, traceback.format_exc()),
            xbmc.LOGERROR,
        )
        xbmcgui.Dialog().ok(
            ADDON.getAddonInfo("name"),
            "An unexpected error occurred. Check the Kodi log for details.",
        )
        xbmcplugin.endOfDirectory(HANDLE, succeeded=False, cacheToDisc=False)


if __name__ == "__main__":
    main()
