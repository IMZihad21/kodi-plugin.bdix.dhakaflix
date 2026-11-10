"""Portal category discovery and bounded-concurrency listing traversal."""

import html
from collections import deque
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from html.parser import HTMLParser
from urllib.parse import urljoin

from resources.lib.portal import PortalError
from resources.lib.urls import URLValidationError, http_origin, normalize_http_url

from .models import Category, Media, SyncCancelled, SyncError

MAX_CRAWL_CONCURRENCY = 12
_EXCLUDED_LABELS = ("game", "software", "tutorial")
_EXCLUDED_SERIES_MARKERS = ("documentar", "wwe", "aew", "wrestl")
_EXCLUDED_CATEGORY_MARKERS = ("documentar", "wwe", "aew", "wrestl")
_TV_LABELS = (
    "series",
    "tv",
    "anime",
    "cartoon",
    "documentary",
    "wrestling",
    "award",
    "show",
)


class _AnchorParser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.anchors = []
        self._anchor = None

    def handle_starttag(self, tag, attrs):
        if tag == "a":
            self._anchor = [dict(attrs).get("href", ""), []]

    def handle_data(self, data):
        if self._anchor is not None:
            self._anchor[1].append(data)

    def handle_entityref(self, name):
        if self._anchor is not None:
            self._anchor[1].append(html.unescape(f"&{name};"))

    def handle_endtag(self, tag):
        if tag == "a" and self._anchor is not None:
            href, text = self._anchor
            label = " ".join("".join(text).split())
            self.anchors.append((href, label))
            self._anchor = None


def _approved_origin(url, allowed_origins):
    try:
        return http_origin(normalize_http_url(url)) in allowed_origins
    except URLValidationError:
        return False


def _is_excluded_category(label):
    normalized_label = " ".join(label.casefold().split())
    if any(word in normalized_label for word in _EXCLUDED_LABELS):
        return True
    if any(marker in normalized_label for marker in _EXCLUDED_CATEGORY_MARKERS):
        return True
    return "award" in normalized_label and (
        "tv" in normalized_label or "show" in normalized_label
    )


def _is_excluded_series_entry(name):
    normalized_name = name.casefold()
    return any(marker in normalized_name for marker in _EXCLUDED_SERIES_MARKERS)


def discover_categories(html_text, homepage_url, allowed_origins=None):
    parser = _AnchorParser()
    parser.feed(html_text)
    allowed_origins = allowed_origins or {http_origin(homepage_url)}
    normalized_homepage_url = normalize_http_url(homepage_url)
    categories = {}
    for href, label in parser.anchors:
        normalized_label = " ".join(label.casefold().split())
        if not href or not normalized_label:
            continue
        if _is_excluded_category(normalized_label):
            continue
        if "movie" in normalized_label or "film" in normalized_label:
            media_type = "movie"
        elif any(word in normalized_label for word in _TV_LABELS):
            media_type = "series"
        else:
            continue
        try:
            target_url = normalize_http_url(urljoin(homepage_url, href))
        except URLValidationError:
            continue
        if not _approved_origin(target_url, allowed_origins):
            continue
        if target_url == normalized_homepage_url:
            continue
        categories.setdefault(target_url, Category(label, target_url, media_type))
    return list(categories.values())


def _is_video(entry):
    return entry.kind == "video" or entry.name.casefold().endswith(
        (
            ".3gp",
            ".avi",
            ".flv",
            ".m2ts",
            ".m4v",
            ".mkv",
            ".mov",
            ".mp4",
            ".mpeg",
            ".mpg",
            ".mts",
            ".ogv",
            ".ts",
            ".webm",
            ".wmv",
        )
    )


def crawl_catalog(
    client,
    homepage_url,
    cancelled=None,
    progress=None,
    max_concurrency=6,
    previous_directories=None,
    full_crawl=True,
    media_types=("movie", "series"),
):
    cancelled = cancelled or (lambda: False)
    progress = progress or (lambda page_count, media_count, category: None)
    homepage_html, final_homepage_url = client.fetch_html(homepage_url)
    allowed_origins = getattr(client, "allowed_origins", None)
    if not allowed_origins:
        allowed_origins = {http_origin(final_homepage_url)}
    categories = discover_categories(homepage_html, final_homepage_url, allowed_origins)
    all_category_urls = {category.url for category in categories}
    categories = [
        category for category in categories if category.media_type in media_types
    ]
    if not categories:
        raise SyncError(
            "The portal homepage contains no supported movie or TV categories"
        )

    previous_directories = previous_directories or {}
    directory_states = {} if full_crawl else dict(previous_directories)
    children_by_parent = {}

    def set_directory_state(url, modified, parent_url):
        state = {"modified": modified, "parent_url": parent_url}
        directory_states[url] = state
        if parent_url is not None:
            children_by_parent.setdefault(parent_url, set()).add(url)

    def remove_directory_subtree(root_url):
        pending = [root_url]
        while pending:
            current_url = pending.pop()
            pending.extend(children_by_parent.pop(current_url, ()))
            state = directory_states.pop(current_url, None)
            if state is not None and state["parent_url"] is not None:
                children_by_parent.get(state["parent_url"], set()).discard(current_url)

    for directory_url, directory_state in directory_states.items():
        parent_url = directory_state.get("parent_url")
        if parent_url is not None:
            children_by_parent.setdefault(parent_url, set()).add(directory_url)

    category_urls = {category.url for category in categories}
    if not full_crawl:
        previous_category_urls = {
            directory_url
            for directory_url, directory_state in previous_directories.items()
            if directory_state.get("parent_url") is None
        }
        missing_categories = previous_category_urls - all_category_urls
        if missing_categories:
            raise SyncError(
                "The portal homepage omitted previously indexed categories; "
                "the existing library was preserved until a full audit"
            )
    for directory_url, directory_state in tuple(directory_states.items()):
        if (
            directory_state.get("parent_url") is None
            and directory_url not in category_urls
        ):
            remove_directory_subtree(directory_url)

    queue = deque((category.url, category, (), None, None) for category in categories)
    visited = set()
    media_by_url = {}
    errors = []
    concurrency = min(max(1, int(max_concurrency)), MAX_CRAWL_CONCURRENCY)
    with ThreadPoolExecutor(max_workers=concurrency) as executor:
        futures = {}
        while queue or futures:
            if cancelled():
                for future in futures:
                    future.cancel()
                raise SyncCancelled("Sync cancelled because Kodi is shutting down")
            while queue and len(futures) < concurrency:
                url, category, path_parts, parent_url, modified = queue.popleft()
                if url in visited:
                    continue
                if (
                    not full_crawl
                    and modified
                    and previous_directories.get(url, {}).get("modified") == modified
                ):
                    continue
                if not _approved_origin(url, allowed_origins):
                    errors.append(
                        f"{url}: directory is outside the approved server list"
                    )
                    continue
                visited.add(url)
                set_directory_state(url, modified, parent_url)
                future = executor.submit(client.listing, url, None, True)
                futures[future] = (url, category, path_parts)
            if not futures:
                continue
            completed, _ = wait(futures, return_when=FIRST_COMPLETED)
            for future in completed:
                url, category, path_parts = futures.pop(future)
                try:
                    entries = future.result()
                except PortalError as error:
                    errors.append(f"{url}: {error}")
                    progress(len(visited), len(media_by_url), category.label)
                    continue
                previous_children = set(children_by_parent.get(url, ()))
                current_children = set()
                for entry in entries:
                    if entry.kind == "folder":
                        if not (
                            category.media_type == "series"
                            and _is_excluded_series_entry(entry.name)
                        ) and _approved_origin(entry.url, allowed_origins):
                            current_children.add(entry.url)
                            set_directory_state(entry.url, entry.modified, url)
                            queue.append(
                                (
                                    entry.url,
                                    category,
                                    (*path_parts, entry.name),
                                    url,
                                    entry.modified,
                                )
                            )
                    elif (
                        _is_video(entry)
                        and not (
                            category.media_type == "series"
                            and _is_excluded_series_entry(entry.name)
                        )
                        and _approved_origin(entry.url, allowed_origins)
                    ):
                        media = Media(
                            entry.name,
                            entry.url,
                            category.media_type,
                            category.label,
                            path_parts,
                            url,
                        )
                        previous = media_by_url.get(media.url)
                        if previous is None or (
                            media.media_type == "series"
                            and previous.media_type != "series"
                        ):
                            media_by_url[media.url] = media
                for removed_url in previous_children - current_children:
                    remove_directory_subtree(removed_url)
                progress(len(visited), len(media_by_url), category.label)
    return (
        categories,
        sorted(media_by_url.values(), key=lambda media: media.url),
        len(visited),
        tuple(errors),
        directory_states,
        visited,
    )
