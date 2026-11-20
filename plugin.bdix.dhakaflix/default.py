#!/usr/bin/env python
"""Kodi plugin entry point for DhakaFlix."""

import sys
import threading
import traceback
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from urllib.parse import unquote, urlsplit

import xbmc
import xbmcaddon
import xbmcgui
import xbmcplugin
import xbmcvfs

from resources.lib.cache import CacheError, ListingCache, clear_memory_cache
from resources.lib.catalog import NODES, PORTAL_URL, categories, search_nodes
from resources.lib.config import parse_integer_setting
from resources.lib.parser import (
    format_kodi_date,
    format_size,
    parse_media_metadata,
)
from resources.lib.portal import PortalClient, PortalError
from resources.lib.router import RouteError, parse_route, plugin_url
from resources.lib.urls import (
    URLValidationError,
    http_origin,
    normalize_http_url,
    parent_directory_url,
)

ADDON_ID = "plugin.bdix.dhakaflix"
_BATCH_SIZE = 1000
_STATE_LOCK = threading.Lock()
_CLIENT_STATE = {"key": None, "client": None}
_CACHE_STATE = {"key": None, "cache": None}


@dataclass(frozen=True, slots=True)
class _Context:
    addon: object
    addon_name: str
    handle: int
    base_url: str


def reset_runtime_state():
    """Close reusable client pools and clear cached runtime state."""
    with _STATE_LOCK:
        client = _CLIENT_STATE["client"]
        _CLIENT_STATE["key"] = None
        _CLIENT_STATE["client"] = None
        _CACHE_STATE["key"] = None
        _CACHE_STATE["cache"] = None
    if client is not None:
        client.close()
    clear_memory_cache()


def _log(message, level=xbmc.LOGINFO):
    xbmc.log(f"[{ADDON_ID}] {message}", level)


def _notify(ctx, message):
    xbmcgui.Dialog().notification(ctx.addon_name, message)


def _notify_error(ctx, message, details=None):
    text = f"{message}\n\n{details}" if details else message
    xbmcgui.Dialog().ok(ctx.addon_name, text)


def _settings(addon):
    node_urls = {}
    for node_id, node in NODES.items():
        value = addon.getSetting(node["setting"]).strip()
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
        for word in addon.getSetting("excluded_keywords").split(",")
        if word.strip()
    ]
    cache_ttl = parse_integer_setting(
        addon.getSetting("cache_ttl"),
        name="Listing cache duration",
        default=7200,
        minimum=0,
    )
    timeout = parse_integer_setting(
        addon.getSetting("request_timeout"),
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


def _get_client(origin_url, exclusions, timeout, version, node_urls):
    key = (
        origin_url,
        tuple(exclusions),
        timeout,
        version,
        tuple(sorted(node_urls.items())),
    )
    old_client = None
    with _STATE_LOCK:
        if _CLIENT_STATE["key"] == key and _CLIENT_STATE["client"] is not None:
            return _CLIENT_STATE["client"]
        old_client = _CLIENT_STATE["client"]
        client = PortalClient(
            origin_url=origin_url,
            excluded_keywords=exclusions,
            timeout=timeout,
            user_agent=f"Kodi DhakaFlix plugin/{version}",
            warning_callback=lambda message: _log(message, xbmc.LOGWARNING),
            allowed_origins=(origin_url, *node_urls.values()),
        )
        _CLIENT_STATE["key"] = key
        _CLIENT_STATE["client"] = client
    if old_client is not None:
        old_client.close()
    return client


def _get_cache(addon, ttl_seconds):
    profile = xbmcvfs.translatePath(addon.getAddonInfo("profile"))
    key = (profile, ttl_seconds)
    with _STATE_LOCK:
        if _CACHE_STATE["key"] == key and _CACHE_STATE["cache"] is not None:
            return _CACHE_STATE["cache"]
        cache = ListingCache(profile, ttl_seconds=ttl_seconds)
        _CACHE_STATE["key"] = key
        _CACHE_STATE["cache"] = cache
        return cache


def _route_url(ctx, action, target_url=None, media_type=None):
    return plugin_url(
        ctx.base_url,
        action,
        target_url,
        media_type=media_type,
    )


def _make_listitem(label="", label2="", path=""):
    try:
        return xbmcgui.ListItem(
            label=label,
            label2=label2,
            path=path,
            offscreen=True,
        )
    except TypeError:
        item = xbmcgui.ListItem(label=label, path=path)
        if label2 and hasattr(item, "setLabel2"):
            item.setLabel2(label2)
        return item


def _set_video_info(
    item,
    title,
    mediatype=None,
    studio=None,
    year=None,
    season=None,
    episode=None,
    date_added=None,
):
    if hasattr(item, "getVideoInfoTag"):
        tag = item.getVideoInfoTag()
        tag.setTitle(title)
        if mediatype and hasattr(tag, "setMediaType"):
            tag.setMediaType(mediatype)
        if year is not None and hasattr(tag, "setYear"):
            tag.setYear(year)
        if season is not None and hasattr(tag, "setSeason"):
            tag.setSeason(season)
        if episode is not None and hasattr(tag, "setEpisode"):
            tag.setEpisode(episode)
        if date_added and hasattr(tag, "setDateAdded"):
            tag.setDateAdded(date_added)
        if studio:
            if hasattr(tag, "setStudios"):
                tag.setStudios([studio])
            elif hasattr(tag, "setStudio"):
                tag.setStudio(studio)
        return
    info = {"title": title}
    if mediatype:
        info["mediatype"] = mediatype
    if year is not None:
        info["year"] = year
    if season is not None:
        info["season"] = season
    if episode is not None:
        info["episode"] = episode
    if date_added:
        info["dateadded"] = date_added
    if studio:
        info["studio"] = studio
    item.setInfo("video", info)


def _list_item(entry, artwork_url=None):
    is_video = entry.kind == "video"
    label2 = format_size(entry.size) if is_video else ""
    item = _make_listitem(label=entry.name, label2=label2, path=entry.url)
    if artwork_url:
        item.setArt({"thumb": artwork_url, "icon": artwork_url})
    date_added = format_kodi_date(entry.modified)
    if is_video:
        clean_title, year, season, episode, mediatype = parse_media_metadata(entry.name)
        _set_video_info(
            item,
            clean_title,
            mediatype=mediatype,
            year=year,
            season=season,
            episode=episode,
            date_added=date_added,
        )
        item.setProperty("IsPlayable", "true")
    else:
        _set_video_info(item, entry.name, date_added=date_added)
    return item


def _listing_context_menu(ctx, target_url):
    return [
        ("Refresh listing", f"RunPlugin({_route_url(ctx, 'refresh', target_url)})"),
        ("Clear listing cache", f"RunPlugin({_route_url(ctx, 'clear_cache')})"),
    ]


def _finish_directory(
    ctx,
    items,
    content="videos",
    succeeded=True,
    cache_to_disc=False,
    sort_methods=(),
):
    xbmcplugin.setContent(ctx.handle, content)
    add_sort_method = getattr(xbmcplugin, "addSortMethod", None)
    if add_sort_method is not None:
        for method_name in sort_methods:
            method = getattr(xbmcplugin, method_name, None)
            if method is not None:
                add_sort_method(ctx.handle, method)
    add_items = getattr(xbmcplugin, "addDirectoryItems", None)
    if add_items is not None:
        total = len(items)
        for start in range(0, total, _BATCH_SIZE):
            add_items(ctx.handle, items[start : start + _BATCH_SIZE], total)
    else:
        for route, item, is_folder in items:
            xbmcplugin.addDirectoryItem(
                handle=ctx.handle,
                url=route,
                listitem=item,
                isFolder=is_folder,
            )
    xbmcplugin.endOfDirectory(
        ctx.handle,
        succeeded=succeeded,
        updateListing=False,
        cacheToDisc=cache_to_disc,
    )


def _render_entries(ctx, target_url, entries):
    visible = [entry for entry in entries if entry.kind in ("folder", "video")]
    images = [entry for entry in entries if entry.kind == "image"]
    artwork = _select_artwork(images)
    subtitles = [entry for entry in entries if entry.kind == "subtitle"]
    context_menu = _listing_context_menu(ctx, target_url)
    items = []
    for entry in visible:
        item = _list_item(entry, artwork)
        item.addContextMenuItems(context_menu)
        if entry.kind == "folder":
            items.append((_route_url(ctx, "browse", entry.url), item, True))
        else:
            matches = _matching_subtitles(entry.name, subtitles)
            if matches:
                item.setSubtitles([subtitle.url for subtitle in matches])
            items.append((_route_url(ctx, "play", entry.url), item, False))

    _finish_directory(
        ctx,
        items,
        sort_methods=(
            "SORT_METHOD_UNSORTED",
            "SORT_METHOD_LABEL_IGNORE_FOLDERS",
            "SORT_METHOD_DATEADDED",
        ),
    )
    if not visible:
        _notify(ctx, "No video or folder entries found")


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


def _browse(ctx, client, cache, target_url, refresh=False):
    entries = client.listing(target_url, cache, refresh=refresh)
    _render_entries(ctx, target_url, entries)


def _menu_item(label, route, is_folder=True):
    item = _make_listitem(label=label)
    _set_video_info(item, label)
    return route, item, is_folder


def _render_home(ctx, origin_url):
    items = [
        _menu_item(
            "Browse DhakaFlix portal",
            _route_url(ctx, "browse", origin_url),
        ),
        _menu_item(
            "Movie categories",
            _route_url(ctx, "catalog", media_type="movie"),
        ),
        _menu_item(
            "TV & series categories",
            _route_url(ctx, "catalog", media_type="series"),
        ),
        _menu_item("Search movies", _route_url(ctx, "search", media_type="movie")),
        _menu_item(
            "Clear listing cache",
            _route_url(ctx, "clear_cache"),
            is_folder=False,
        ),
        _menu_item("Add-on settings", _route_url(ctx, "settings"), is_folder=False),
    ]
    _finish_directory(ctx, items, content="videos")


def _render_catalog(ctx, media_type, node_urls):
    items = [
        _menu_item(label, _route_url(ctx, "browse", target_url))
        for label, target_url in categories(media_type, node_urls)
    ]
    _finish_directory(ctx, items, content="videos")
    if not items:
        _notify(ctx, "No categories are available")


def _search_dialog(ctx, media_type, client, node_urls):
    keyboard = xbmc.Keyboard("", "Search movies")
    keyboard.doModal()
    if not keyboard.isConfirmed():
        xbmcplugin.endOfDirectory(ctx.handle, succeeded=False, cacheToDisc=False)
        return
    query = keyboard.getText().strip()
    if not query:
        xbmcplugin.endOfDirectory(ctx.handle, succeeded=False, cacheToDisc=False)
        return
    _search(ctx, client, media_type, query, node_urls)


def _search(ctx, client, media_type, query, node_urls):
    nodes = search_nodes(media_type, node_urls)
    if not nodes:
        _finish_directory(ctx, [])
        _notify(ctx, "No search sources are available")
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
        clean_title, year, season, episode, mediatype = parse_media_metadata(entry.name)
        item = _make_listitem(
            label=entry.name,
            label2=format_size(entry.size),
            path=entry.url,
        )
        _set_video_info(
            item,
            clean_title,
            mediatype=mediatype,
            studio=f"DhakaFlix node {node_id}",
            year=year,
            season=season,
            episode=episode,
            date_added=format_kodi_date(entry.modified),
        )
        item.setProperty("IsPlayable", "true")
        parent_route = _route_url(ctx, "browse", parent_directory_url(entry.url))
        item.addContextMenuItems(
            [("Open parent folder", f"Container.Update({parent_route})")]
        )
        items.append((_route_url(ctx, "play", entry.url), item, False))

    _finish_directory(
        ctx,
        items,
        cache_to_disc=True,
        sort_methods=(
            "SORT_METHOD_LABEL_IGNORE_FOLDERS",
            "SORT_METHOD_DATEADDED",
        ),
    )
    if errors:
        if items:
            _notify(ctx, "Some search sources could not be reached")
        else:
            _notify(ctx, f"Search failed: {errors[0]}")
    elif not items:
        _notify(ctx, "No matching videos found")


def _play(ctx, cache, target_url):
    url_filename = unquote(urlsplit(target_url).path.rsplit("/", 1)[-1])
    parent_entries = None
    matched = None
    if cache is not None:
        parent_url = parent_directory_url(target_url)
        try:
            parent_entries = cache.get_any(parent_url)
        except CacheError as error:
            _log(
                f"Ignoring unreadable listing cache during playback: {error}",
                xbmc.LOGWARNING,
            )
            parent_entries = None
        if parent_entries:
            matched = next(
                (entry for entry in parent_entries if entry.url == target_url),
                None,
            )

    display_name = matched.name if matched is not None else url_filename
    clean_title, year, season, episode, mediatype = parse_media_metadata(display_name)
    item = _make_listitem(label=display_name, path=target_url)
    item.setPath(target_url)

    date_added = format_kodi_date(matched.modified) if matched is not None else None
    if parent_entries:
        artwork = _select_artwork(
            [entry for entry in parent_entries if entry.kind == "image"]
        )
        if artwork:
            item.setArt({"thumb": artwork, "icon": artwork})
        subtitles = [entry for entry in parent_entries if entry.kind == "subtitle"]
        matches = _matching_subtitles(display_name, subtitles)
        if matches:
            item.setSubtitles([subtitle.url for subtitle in matches])

    _set_video_info(
        item,
        clean_title,
        mediatype=mediatype,
        year=year,
        season=season,
        episode=episode,
        date_added=date_added,
    )
    item.setProperty("IsPlayable", "true")
    xbmcplugin.setResolvedUrl(ctx.handle, True, item)


def main(argv=None):
    arguments = sys.argv if argv is None else argv
    base_url = arguments[0]
    handle = int(arguments[1]) if len(arguments) > 1 else -1
    query_string = arguments[2] if len(arguments) > 2 else ""
    addon = xbmcaddon.Addon(id=ADDON_ID)
    ctx = _Context(
        addon=addon,
        addon_name=addon.getAddonInfo("name"),
        handle=handle,
        base_url=base_url,
    )
    try:
        route = parse_route(ctx.base_url, query_string)
        origin_url, exclusions, cache_ttl, timeout, node_urls = _settings(addon)
        version = addon.getAddonInfo("version")
        cache = _get_cache(addon, cache_ttl)

        if route.action == "root":
            _render_home(ctx, origin_url)
        elif route.action == "settings":
            addon.openSettings()
            if ctx.handle >= 0:
                xbmcplugin.endOfDirectory(ctx.handle, succeeded=True, cacheToDisc=False)
        elif route.action == "clear_cache":
            cache.clear()
            _notify(ctx, "Listing cache cleared")
            if ctx.handle >= 0:
                xbmcplugin.endOfDirectory(
                    ctx.handle,
                    succeeded=False,
                    cacheToDisc=False,
                )
        elif route.action == "play":
            allowed_origins = {
                http_origin(value)
                for value in (origin_url, *node_urls.values())
                if value
            }
            if http_origin(route.target_url) not in allowed_origins:
                raise RouteError("Playback URL is not on an approved DhakaFlix server")
            _play(ctx, cache, route.target_url)
        elif route.action == "catalog":
            _render_catalog(ctx, route.media_type, node_urls)
        elif route.action == "search":
            client = _get_client(origin_url, exclusions, timeout, version, node_urls)
            _search_dialog(ctx, route.media_type, client, node_urls)
        elif route.action in ("browse", "refresh"):
            target_url = route.target_url or origin_url
            if route.action == "refresh" and ctx.handle < 0:
                cache.invalidate(target_url)
                xbmc.executebuiltin("Container.Refresh")
                return
            client = _get_client(origin_url, exclusions, timeout, version, node_urls)
            _browse(ctx, client, cache, target_url, refresh=route.action == "refresh")
        else:
            raise RouteError("Unsupported action")
    except (PortalError, RouteError, ValueError, CacheError) as error:
        _log(str(error), xbmc.LOGERROR)
        _notify_error(ctx, str(error))
        if ctx.handle >= 0:
            xbmcplugin.endOfDirectory(ctx.handle, succeeded=False, cacheToDisc=False)
    except Exception as error:  # noqa: BLE001
        _log(
            f"Unexpected add-on error: {error}\n{traceback.format_exc()}",
            xbmc.LOGERROR,
        )
        _notify_error(
            ctx,
            "An unexpected error occurred. Check the Kodi log for details.",
        )
        if ctx.handle >= 0:
            xbmcplugin.endOfDirectory(ctx.handle, succeeded=False, cacheToDisc=False)


if __name__ == "__main__":
    main()
