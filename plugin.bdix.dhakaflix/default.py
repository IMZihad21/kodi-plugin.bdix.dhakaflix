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
from resources.lib.catalog import NODES, PORTAL_URL, categories, search_nodes
from resources.lib.config import parse_integer_setting
from resources.lib.library import (
    SyncRequestError,
    enqueue_manual_sync,
    library_paths,
)
from resources.lib.portal import PortalClient, PortalError
from resources.lib.router import RouteError, parse_route, plugin_url
from resources.lib.urls import URLValidationError, http_origin, normalize_http_url

ADDON_ID = "plugin.bdix.dhakaflix"
ADDON = xbmcaddon.Addon(id=ADDON_ID)
HANDLE = int(sys.argv[1])
BASE_URL = sys.argv[0]


def _log(message, level=xbmc.LOGINFO):
    xbmc.log(f"[{ADDON_ID}] {message}", level)


def _notify(message):
    xbmcgui.Dialog().notification(ADDON.getAddonInfo("name"), message)


def _notify_error(message, details=None):
    text = f"{message}\n\n{details}" if details else message
    xbmcgui.Dialog().ok(ADDON.getAddonInfo("name"), text)


def _settings():
    node_urls = {}
    for node_id, node in NODES.items():
        value = ADDON.getSetting(node["setting"]).strip()
        if not value:
            node_urls[node_id] = ""
            continue
        try:
            node_urls[node_id] = normalize_http_url(value)
        except URLValidationError as error:
            raise ValueError(
                f"Invalid DhakaFlix node {node_id} URL: {error}"
            ) from error

    excluded_keywords = [
        word.strip().casefold()
        for word in ADDON.getSetting("excluded_keywords").split(",")
        if word.strip()
    ]
    cache_ttl = parse_integer_setting(
        ADDON.getSetting("cache_ttl"),
        name="Listing cache duration",
        default=7200,
        minimum=0,
    )
    timeout = parse_integer_setting(
        ADDON.getSetting("request_timeout"),
        name="Request timeout",
        default=20,
        minimum=3,
        maximum=60,
    )
    return (
        PORTAL_URL,
        excluded_keywords,
        cache_ttl,
        timeout,
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


def _set_video_info(item, title, mediatype=None, studio=None):
    if hasattr(item, "getVideoInfoTag"):
        tag = item.getVideoInfoTag()
        tag.setTitle(title)
        if mediatype and hasattr(tag, "setMediaType"):
            tag.setMediaType(mediatype)
        if studio:
            if hasattr(tag, "setStudios"):
                tag.setStudios([studio])
            elif hasattr(tag, "setStudio"):
                tag.setStudio(studio)
        return
    info = {"title": title}
    if mediatype:
        info["mediatype"] = mediatype
    if studio:
        info["studio"] = studio
    item.setInfo("video", info)


def _list_item(entry, artwork_url=None):
    item = xbmcgui.ListItem(label=entry.name, path=entry.url)
    if artwork_url:
        item.setArt({"thumb": artwork_url, "icon": artwork_url})
    _set_video_info(item, entry.name)
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
    _set_video_info(item, label)
    return route, item, is_folder


def _render_home(origin_url):
    items = [
        _menu_item("Browse DhakaFlix portal", _route_url("browse", origin_url)),
        _menu_item("Movie categories", _route_url("catalog", media_type="movie")),
        _menu_item(
            "TV & series categories", _route_url("catalog", media_type="series")
        ),
        _menu_item("Search movies", _route_url("search", media_type="movie")),
        _menu_item("Search TV & series", _route_url("search", media_type="series")),
        _menu_item("Incremental library sync", _route_url("sync"), is_folder=False),
        _menu_item(
            "Full library rescan",
            _route_url("full_sync"),
            is_folder=False,
        ),
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
        _notify("No categories are available")


def _search_dialog(media_type, client, node_urls):
    heading = "movies" if media_type == "movie" else "TV & series"
    keyboard = xbmc.Keyboard("", f"Search {heading}")
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
        _notify("No search sources are available")
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
                errors.append(f"Node {node_id}: {error}")
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
        _set_video_info(
            item,
            entry.name,
            mediatype="video",
            studio=f"DhakaFlix node {node_id}",
        )
        items.append((_route_url("play", entry.url), item, False))

    _finish_directory(items)
    if errors:
        if items:
            _notify("Some search sources could not be reached")
        else:
            _notify(f"Search failed: {errors[0]}")
    elif not items:
        _notify("No matching videos found")


def _play(target_url):
    item = xbmcgui.ListItem(path=target_url)
    item.setPath(target_url)
    title = unquote(urlsplit(target_url).path.rsplit("/", 1)[-1])
    _set_video_info(item, title)
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
            warning_callback=lambda message: _log(message, xbmc.LOGWARNING),
            allowed_origins=(origin_url, *node_urls.values()),
        )
        cache = _cache(cache_ttl)

        if route.action == "root":
            _render_home(origin_url)
        elif route.action == "settings":
            ADDON.openSettings()
            xbmcplugin.endOfDirectory(HANDLE, succeeded=True, cacheToDisc=False)
        elif route.action in ("sync", "full_sync"):
            profile = xbmcvfs.translatePath(ADDON.getAddonInfo("profile"))
            full_crawl = route.action == "full_sync"
            queued = enqueue_manual_sync(
                library_paths(profile)["state"],
                full_crawl=full_crawl,
            )
            _notify(
                (
                    "Full library rescan queued"
                    if full_crawl
                    else "Incremental library sync queued"
                )
                if queued
                else "A library sync is already queued"
            )
            xbmcplugin.endOfDirectory(HANDLE, succeeded=queued, cacheToDisc=False)
        elif route.action == "play":
            allowed_origins = {
                http_origin(value)
                for value in (origin_url, *node_urls.values())
                if value
            }
            if http_origin(route.target_url) not in allowed_origins:
                raise RouteError("Playback URL is not on an approved DhakaFlix server")
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
    except (PortalError, RouteError, ValueError, SyncRequestError) as error:
        _log(str(error), xbmc.LOGERROR)
        _notify_error(str(error))
        xbmcplugin.endOfDirectory(HANDLE, succeeded=False, cacheToDisc=False)
    except Exception as error:  # noqa: BLE001
        _log(
            f"Unexpected add-on error: {error}\n{traceback.format_exc()}",
            xbmc.LOGERROR,
        )
        _notify_error("An unexpected error occurred. Check the Kodi log for details.")
        xbmcplugin.endOfDirectory(HANDLE, succeeded=False, cacheToDisc=False)


if __name__ == "__main__":
    main()
