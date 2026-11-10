"""Kodi-independent discovery, crawl, and STRM library generation."""

import hashlib
import html
import json
import os
import re
import shutil
import tempfile
import time
import unicodedata
import uuid
from collections import deque
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from dataclasses import dataclass
from html.parser import HTMLParser
from urllib.parse import unquote, urljoin, urlsplit

from resources.lib.library_index import LibraryIndex
from resources.lib.portal import PortalError
from resources.lib.urls import URLValidationError, http_origin, normalize_http_url

MAX_LIBRARY_PATH_LENGTH = 240
MAX_COMPONENT_BYTES = 180
MAX_CRAWL_PAGES = 50000
MAX_CRAWL_DEPTH = 48
MAX_CRAWL_MEDIA = 100000
MAX_CRAWL_CATEGORIES = 256
MAX_CRAWL_DURATION = 60 * 60
MAX_CRAWL_CONCURRENCY = 12
FULL_CRAWL_INTERVAL = 7 * 24 * 60 * 60
DELETION_CONFIRMATION_INTERVAL = 30 * 60
DELETION_CONFIRMATION_RATIO = 0.30
DELETION_CONFIRMATION_MIN_MEDIA = 20
_TRANSACTION_NAME = "sync.transaction.json"
_TRANSACTION_DIRECTORY = ".sync-transaction"
_VIDEO_EXTENSIONS = (
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
_EPISODE_PATTERN = re.compile(r"(?i)\bS(\d{1,2})E(\d{1,3})\b")
_SEASON_PATTERN = re.compile(r"(?i)^season\s*(\d{1,2})(?:\b.*)?$")
_SERIES_INDEX_PATTERN = re.compile(r"(?i)^(?:[a-z](?:-[a-z])?|0-9|#)$")
_YEAR_PATTERN = re.compile(r"\b(19\d{2}|20\d{2})\b")
_QUALITY_PATTERN = re.compile(
    r"(?i)(?:[\s._-]+\b(?:480p|576p|720p|1080p|1440p|2160p|4k|8k|"
    r"web[- ]?dl|web[- ]?rip|blu[- ]?ray|brrip|hdrip|dvdrip|"
    r"x264|x265|h\.?264|h\.?265|hevc|10bit|dual audio)\b)"
)
_INVALID_COMPONENT = re.compile(r'[<>:"/\\|?*\x00-\x1f]')


class SyncError(RuntimeError):
    """Raised when a library sync cannot safely complete."""


class SyncCancelled(SyncError):
    """Raised when Kodi is shutting down or a sync was cancelled."""


@dataclass(frozen=True)
class Category:
    label: str
    url: str
    media_type: str


@dataclass(frozen=True)
class Media:
    name: str
    url: str
    media_type: str
    category: str
    path_parts: tuple
    source_directory: str = ""


@dataclass(frozen=True)
class SyncResult:
    complete: bool
    movie_count: int
    episode_count: int
    page_count: int
    errors: tuple
    pruned_count: int


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
        if target_url == normalize_http_url(homepage_url):
            continue
        categories.setdefault(target_url, Category(label, target_url, media_type))
    return list(categories.values())


def _is_video(entry):
    return entry.kind == "video" or entry.name.casefold().endswith(_VIDEO_EXTENSIONS)


def crawl_catalog(
    client,
    homepage_url,
    cancelled=None,
    progress=None,
    max_concurrency=6,
    previous_directories=None,
    full_crawl=True,
    max_pages=MAX_CRAWL_PAGES,
    max_depth=MAX_CRAWL_DEPTH,
    max_media=MAX_CRAWL_MEDIA,
    max_categories=MAX_CRAWL_CATEGORIES,
    max_duration=MAX_CRAWL_DURATION,
    media_types=("movie", "series"),
):
    cancelled = cancelled or (lambda: False)
    progress = progress or (lambda page_count, media_count, category: None)
    started_at = time.monotonic()
    homepage_html, final_homepage_url = client.fetch_html(homepage_url)
    allowed_origins = getattr(client, "allowed_origins", None)
    if not allowed_origins:
        allowed_origins = {http_origin(final_homepage_url)}
    categories = discover_categories(homepage_html, final_homepage_url, allowed_origins)
    all_category_urls = {category.url for category in categories}
    categories = [
        category for category in categories if category.media_type in media_types
    ]
    if len(categories) > max_categories:
        raise SyncError(f"The portal exceeded the {max_categories} category limit")
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
    with ThreadPoolExecutor(max_workers=max(1, int(max_concurrency))) as executor:
        futures = {}
        while queue or futures:
            if cancelled():
                for future in futures:
                    future.cancel()
                raise SyncCancelled("Sync cancelled because Kodi is shutting down")
            if time.monotonic() - started_at > max_duration:
                raise SyncError(f"The catalog crawl exceeded the {max_duration}-second deadline")
            while queue and len(futures) < min(max(1, int(max_concurrency)), MAX_CRAWL_CONCURRENCY):
                url, category, path_parts, parent_url, modified = queue.popleft()
                if url in visited:
                    continue
                if (
                    not full_crawl
                    and modified
                    and previous_directories.get(url, {}).get("modified") == modified
                ):
                    continue
                if len(visited) >= max_pages:
                    raise SyncError(f"The catalog crawl exceeded the {max_pages}-page limit")
                if len(path_parts) > max_depth:
                    raise SyncError(f"The catalog crawl exceeded the {max_depth}-level folder-depth limit")
                if not _approved_origin(url, allowed_origins):
                    errors.append(f"{url}: directory is outside the approved server list")
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
                            if len(directory_states) > max_pages:
                                raise SyncError(
                                    f"The catalog crawl exceeded the {max_pages}-page/directory limit"
                                )
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
                            if previous is None and len(media_by_url) >= max_media:
                                raise SyncError(
                                    f"The catalog crawl exceeded the {max_media}-media limit"
                                )
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


def _safe_component(value, fallback):
    value = _INVALID_COMPONENT.sub("_", value).strip(" .")
    value = re.sub(r"\s+", " ", value)
    if not value:
        value = fallback
    if value.upper() in {
        "CON",
        "PRN",
        "AUX",
        "NUL",
        "COM1",
        "COM2",
        "COM3",
        "COM4",
        "COM5",
        "COM6",
        "COM7",
        "COM8",
        "COM9",
        "LPT1",
        "LPT2",
        "LPT3",
        "LPT4",
        "LPT5",
        "LPT6",
        "LPT7",
        "LPT8",
        "LPT9",
    }:
        value = "_" + value
    value = unicodedata.normalize("NFD", value[:120].rstrip(" .") or fallback)
    encoded = value.encode("utf-8")
    if len(encoded) > MAX_COMPONENT_BYTES:
        suffix = "~" + hashlib.sha256(encoded).hexdigest()[:8]
        prefix = encoded[: MAX_COMPONENT_BYTES - len(suffix)].decode(
            "utf-8",
            errors="ignore",
        )
        value = prefix.rstrip(" .") + suffix
    return value or fallback


def _clean_title(value):
    value = os.path.splitext(value)[0]
    value = re.sub(
        r"(?i)\s*\((?:(?:tv\s+)?(?:mini\s+)?series|tv\s+cartoon)\s+\d{4}(?:\s*[-\u2013\u2014]\s*\d{0,4})?\)",
        "",
        value,
    )
    value = re.sub(
        r"[\[(].*?(?:dual audio|hindi dubbed|multi audio).*?[\])]",
        "",
        value,
        flags=re.IGNORECASE,
    )
    value = _QUALITY_PATTERN.sub("", value)
    value = re.sub(
        r"(?i)[\s._-]+\b(?:pahe|yts|psa|rarbg|evo|web-dl|webrip)\b.*$", "", value
    )
    value = re.sub(r"[._]+", " ", value)
    return " ".join(value.split()).strip(" -_.")


def _media_output_path(media, disambiguate=False):
    filename = unquote(urlsplit(media.url).path.rsplit("/", 1)[-1])
    stem = _clean_title(filename or media.name)
    episode_match = _EPISODE_PATTERN.search(filename)
    if media.media_type == "series":
        season_index = next(
            (
                index
                for index, part in enumerate(media.path_parts)
                if _SEASON_PATTERN.match(part)
            ),
            None,
        )
        show_parts = (
            media.path_parts[:season_index]
            if season_index is not None
            else media.path_parts
        )
        show_candidates = [
            part for part in show_parts if not _SERIES_INDEX_PATTERN.fullmatch(part)
        ]
        show = _clean_title(show_candidates[-1]) if show_candidates else media.category
        if not show:
            show = "Unknown Series"
        show_name = _safe_component(show, "Unknown Series")
        key = hashlib.sha256(media.url.encode("utf-8")).hexdigest()[:8]
        season_name = None
        if episode_match:
            season = int(episode_match.group(1))
            quality_match = re.search(
                r"(?i)\b(480p|576p|720p|1080p|1440p|2160p|4k|8k)\b",
                filename,
            )
            quality = f" {quality_match.group(1)}" if quality_match else ""
            output_name = _safe_component(
                f"{show} S{season:02d}E{int(episode_match.group(2)):02d}"
                f"{quality} [{key}]",
                f"Episode {key}",
            )
            season_name = f"Season {season}"
        else:
            output_name = _safe_component(stem or media.name, f"Episode {key}")
            if disambiguate:
                output_name = _safe_component(
                    f"{output_name} [{key}]",
                    f"Episode {key}",
                )
            if season_index is not None:
                season = _SEASON_PATTERN.match(media.path_parts[season_index])
                season_name = f"Season {int(season.group(1))}"
        output_parts = ["TV Shows", show_name]
        if season_name:
            output_parts.append(season_name)
        output_parts.append(output_name + ".strm")
        return os.path.join(*output_parts), "series"

    parent = media.path_parts[-1] if media.path_parts else stem
    title = _clean_title(parent)
    if not title or _YEAR_PATTERN.fullmatch(title):
        title = stem or media.name
    year = _YEAR_PATTERN.search(parent) or _YEAR_PATTERN.search(filename)
    if year and not _YEAR_PATTERN.search(title):
        title = f"{title} ({year.group(1)})"
    quality_match = re.search(
        r"(?i)\b(480p|576p|720p|1080p|1440p|2160p|4k|8k)\b",
        " ".join(media.path_parts) + " " + filename,
    )
    quality = quality_match.group(1) if quality_match else ""
    key = hashlib.sha256(media.url.encode("utf-8")).hexdigest()[:8]
    category = _safe_component(media.category, "Uncategorized")
    suffix = f" [{key}]" if disambiguate else ""
    folder_name = _safe_component(
        "{}{}{}".format(title, " [" + quality + "]" if quality else "", suffix),
        "Unknown Movie",
    )
    file_name = _safe_component(
        "{}{}{}".format(title, " [" + quality + "]" if quality else "", suffix),
        "Unknown Movie",
    )
    return os.path.join("Movies", category, folder_name, file_name + ".strm"), "movie"


def _limit_output_path(library_dir, relative_path):
    parts = relative_path.split(os.sep)
    original_parts = list(parts)
    while _path_length(os.path.realpath(os.path.join(library_dir, *parts))) > (
        MAX_LIBRARY_PATH_LENGTH
    ):
        candidates = []
        for index in range(1, len(parts)):
            if _SEASON_PATTERN.match(parts[index]):
                continue
            extension = ".strm" if parts[index].casefold().endswith(".strm") else ""
            stem = parts[index][: -len(extension)] if extension else parts[index]
            if len(stem) > 10:
                candidates.append(index)
        if not candidates:
            raise SyncError("The managed library path is too long for this device")
        index = max(candidates, key=lambda value: (len(parts[value]), value))
        extension = ".strm" if parts[index].casefold().endswith(".strm") else ""
        stem = parts[index][: -len(extension)] if extension else parts[index]
        excess = _path_length(os.path.realpath(os.path.join(library_dir, *parts))) - (
            MAX_LIBRARY_PATH_LENGTH
        )
        shortened_length = max(1, len(stem) - excess - 9)
        digest = hashlib.sha256(original_parts[index].encode("utf-8")).hexdigest()[:8]
        parts[index] = stem[:shortened_length] + "~" + digest + extension
    return os.path.join(*parts)


def _path_length(path):
    if os.name == "nt":
        return len(path.encode("utf-16-le")) // 2
    return len(os.fsencode(path))


def _path_key(path):
    return unicodedata.normalize("NFD", path).casefold()


def _native_relative_path(path):
    return path.replace("\\", os.sep).replace("/", os.sep)


def _fsync_directory(path):
    if os.name == "nt":
        return
    try:
        descriptor = os.open(path, os.O_RDONLY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
    except OSError:
        pass


def _fsync_directory_chain(path, root_dir):
    root = os.path.realpath(root_dir)
    current = os.path.realpath(path)
    try:
        if os.path.commonpath((root, current)) != root:
            return
    except ValueError:
        return
    while True:
        _fsync_directory(current)
        if current == root:
            break
        parent = os.path.dirname(current)
        if parent == current:
            break
        current = parent


def _atomic_write(path, data, create_directory=True, durable=True):
    directory = os.path.dirname(path) or os.curdir
    if create_directory:
        os.makedirs(directory, exist_ok=True)
    descriptor, temporary_path = tempfile.mkstemp(prefix=".dhakaflix-", dir=directory)
    try:
        output = os.fdopen(descriptor, "w", encoding="utf-8", newline="\n")
        descriptor = None
        with output:
            output.write(data)
            output.flush()
            if durable:
                os.fsync(output.fileno())
        try:
            os.replace(temporary_path, path)
            if durable:
                _fsync_directory(directory)
        except OSError as error:
            raise OSError(
                f"{error}; atomic replace diagnostics: "
                f"destination={path!r}, destination_length="
                f"{_path_length(os.path.abspath(path))}, "
                f"temporary={temporary_path!r}, temporary_length="
                f"{_path_length(os.path.abspath(temporary_path))}, "
                f"parent_exists={os.path.isdir(directory)}, "
                f"temporary_exists={os.path.exists(temporary_path)}, "
                f"destination_exists={os.path.exists(path)}"
            ) from error
    finally:
        try:
            if descriptor is not None:
                os.close(descriptor)
        finally:
            try:
                os.remove(temporary_path)
            except FileNotFoundError:
                pass


def _load_json(path, default):
    try:
        with open(path, encoding="utf-8") as source:
            return json.load(source)
    except FileNotFoundError:
        return default
    except (OSError, ValueError) as error:
        raise SyncError(f"Unable to read sync state {path}: {error}") from error


def _save_json(path, payload):
    _atomic_write(
        path,
        json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")),
    )


def _managed_path(root_dir, relative_path):
    root = os.path.realpath(root_dir)
    path = os.path.realpath(os.path.join(root, relative_path))
    if os.path.commonpath((root, path)) != root or not path.lower().endswith(".strm"):
        raise SyncError("Refusing to access a path outside the managed library")
    return path


def _transaction_paths(library_dir):
    return (
        os.path.join(library_dir, _TRANSACTION_NAME),
        os.path.join(library_dir, _TRANSACTION_DIRECTORY),
    )


def _prepare_transaction(library_dir, replacements, deletions):
    library_root = os.path.realpath(library_dir)
    journal_path, transaction_dir = _transaction_paths(library_root)
    if os.path.exists(journal_path) or os.path.exists(transaction_dir):
        raise SyncError("An unfinished synchronization transaction must be recovered first")
    staged_root = os.path.join(transaction_dir, "staged")
    backup_root = os.path.join(transaction_dir, "backup")
    os.makedirs(staged_root, exist_ok=False)
    os.makedirs(backup_root, exist_ok=True)
    operations = []
    try:
        index = 0
        for action, target_path, content in replacements:
            relative_target = os.path.relpath(target_path, library_root)
            if relative_target == os.pardir or relative_target.startswith(os.pardir + os.sep):
                raise SyncError("Refusing to modify a path outside the managed library")
            existed = os.path.isfile(target_path)
            staged_relative = os.path.join("staged", f"{index:08d}")
            backup_relative = os.path.join("backup", f"{index:08d}")
            staged_path = os.path.join(transaction_dir, staged_relative)
            backup_path = os.path.join(transaction_dir, backup_relative)
            if action == "replace":
                with open(staged_path, "wb") as target:
                    target.write(content.encode("utf-8"))
                    target.flush()
                    os.fsync(target.fileno())
            if existed:
                shutil.copy2(target_path, backup_path)
                with open(backup_path, "rb") as backup:
                    os.fsync(backup.fileno())
            operations.append(
                {
                    "action": action,
                    "target": relative_target,
                    "existed": existed,
                    "staged": staged_relative if action == "replace" else None,
                    "backup": backup_relative if existed else None,
                }
            )
            index += 1
        for target_path in deletions:
            relative_target = os.path.relpath(target_path, library_root)
            if relative_target == os.pardir or relative_target.startswith(os.pardir + os.sep):
                raise SyncError("Refusing to delete a path outside the managed library")
            existed = os.path.isfile(target_path)
            backup_relative = os.path.join("backup", f"{index:08d}")
            backup_path = os.path.join(transaction_dir, backup_relative)
            if existed:
                shutil.copy2(target_path, backup_path)
                with open(backup_path, "rb") as backup:
                    os.fsync(backup.fileno())
            operations.append(
                {
                    "action": "delete",
                    "target": relative_target,
                    "existed": existed,
                    "staged": None,
                    "backup": backup_relative if existed else None,
                }
            )
            index += 1
        _fsync_directory(staged_root)
        _fsync_directory(backup_root)
        _fsync_directory(transaction_dir)
        transaction_id = uuid.uuid4().hex
        _save_json(
            journal_path,
            {"version": 1, "transaction_id": transaction_id, "operations": operations},
        )
        return transaction_id, operations, transaction_dir, journal_path
    except Exception:
        shutil.rmtree(transaction_dir, ignore_errors=True)
        raise


def _restore_transaction(library_dir, operations, transaction_dir):
    for operation in reversed(operations):
        target_path = _managed_path(library_dir, operation["target"])
        if operation.get("existed"):
            backup_relative = operation.get("backup")
            if not isinstance(backup_relative, str):
                raise SyncError("Synchronization backup metadata is invalid")
            backup_path = os.path.join(transaction_dir, backup_relative)
            if not os.path.isfile(backup_path):
                raise SyncError("A required synchronization backup is missing")
            os.makedirs(os.path.dirname(target_path), exist_ok=True)
            descriptor, temporary_path = tempfile.mkstemp(
                prefix=".dhakaflix-restore-", dir=os.path.dirname(target_path)
            )
            os.close(descriptor)
            try:
                shutil.copy2(backup_path, temporary_path)
                with open(temporary_path, "rb") as restored:
                    os.fsync(restored.fileno())
                os.replace(temporary_path, target_path)
                _fsync_directory(os.path.dirname(target_path))
            except Exception:
                try:
                    os.remove(temporary_path)
                except FileNotFoundError:
                    pass
                raise
        else:
            try:
                os.remove(target_path)
            except FileNotFoundError:
                pass


def _recover_transaction(library_dir, index):
    journal_path, transaction_dir = _transaction_paths(library_dir)
    if not os.path.exists(journal_path):
        if os.path.isdir(transaction_dir):
            shutil.rmtree(transaction_dir)
        return
    journal = _load_json(journal_path, None)
    if (
        not isinstance(journal, dict)
        or journal.get("version") != 1
        or not isinstance(journal.get("transaction_id"), str)
        or not isinstance(journal.get("operations"), list)
    ):
        raise SyncError("The synchronization recovery journal is invalid; library was not modified")
    for index_value, operation in enumerate(journal["operations"]):
        if (
            not isinstance(operation, dict)
            or operation.get("action") not in ("replace", "delete")
            or not isinstance(operation.get("target"), str)
            or not isinstance(operation.get("existed"), bool)
        ):
            raise SyncError("The synchronization recovery journal contains an invalid operation")
        target = operation["target"]
        normalized_target = os.path.normpath(target)
        if (
            os.path.isabs(target)
            or normalized_target in (os.curdir, os.pardir)
            or normalized_target.startswith(os.pardir + os.sep)
            or normalized_target != target
        ):
            raise SyncError("The synchronization recovery journal contains an unsafe path")
        staged = operation.get("staged")
        backup = operation.get("backup")
        expected_staged = os.path.join("staged", f"{index_value:08d}")
        expected_backup = os.path.join("backup", f"{index_value:08d}")
        if operation["action"] == "replace":
            if staged != expected_staged:
                raise SyncError("The synchronization recovery journal contains an invalid staged path")
        elif staged is not None:
            raise SyncError("The synchronization recovery journal contains an unexpected staged path")
        if operation["existed"]:
            if backup != expected_backup:
                raise SyncError("The synchronization recovery journal contains an invalid backup path")
        elif backup is not None:
            raise SyncError("The synchronization recovery journal contains an unexpected backup path")
    if index.is_transaction_committed(journal["transaction_id"]):
        shutil.rmtree(transaction_dir, ignore_errors=True)
        os.remove(journal_path)
        return
    _restore_transaction(library_dir, journal["operations"], transaction_dir)
    shutil.rmtree(transaction_dir, ignore_errors=True)
    os.remove(journal_path)


def _commit_transaction(library_dir, index, replacements, deletions, snapshot):
    transaction_id, operations, transaction_dir, journal_path = _prepare_transaction(
        library_dir, replacements, deletions
    )
    try:
        for operation in operations:
            target_path = _managed_path(library_dir, operation["target"])
            if operation["action"] == "replace":
                staged_path = os.path.join(transaction_dir, operation["staged"])
                os.makedirs(os.path.dirname(target_path), exist_ok=True)
                os.replace(staged_path, target_path)
            else:
                try:
                    os.remove(target_path)
                except FileNotFoundError:
                    pass
            _fsync_directory_chain(os.path.dirname(target_path), library_dir)
        index.save_snapshot(
            snapshot["entries"],
            snapshot["directories"],
            snapshot["previous_directories"],
            snapshot["full_crawl"],
            snapshot["visited"],
            transaction_id=transaction_id,
        )
    except Exception:
        if not index.is_transaction_committed(transaction_id):
            _restore_transaction(library_dir, operations, transaction_dir)
            shutil.rmtree(transaction_dir, ignore_errors=True)
            try:
                os.remove(journal_path)
            except FileNotFoundError:
                pass
        raise
    shutil.rmtree(transaction_dir, ignore_errors=True)
    try:
        os.remove(journal_path)
    except FileNotFoundError:
        pass


def run_sync(
    client,
    library_dir,
    cancelled=None,
    progress=None,
    media_types=("movie", "series"),
):
    media_types = frozenset(media_types)
    if not media_types or not media_types <= {"movie", "series"}:
        raise SyncError("Select at least one valid library media type to sync")
    cancelled = cancelled or (lambda: False)
    progress = progress or (lambda page_count, media_count, category: None)
    os.makedirs(library_dir, exist_ok=True)
    state_path = os.path.join(library_dir, "state.json")
    manifest_path = os.path.join(library_dir, "manifest.json")
    index_path = os.path.join(library_dir, "library.sqlite")
    state = _load_json(state_path, {})
    state["last_attempt"] = time.time()
    _save_json(state_path, state)
    index = None
    try:
        index = LibraryIndex(index_path)
        _recover_transaction(library_dir, index)
        migration_requires_full = index.needs_migration
        if index.needs_migration:
            previous_manifest = _load_json(manifest_path, {"entries": {}})
            if not isinstance(previous_manifest, dict):
                raise SyncError("The previous library manifest is invalid")
            legacy_entries = previous_manifest.get("entries", {})
            previous_directories = previous_manifest.get("directories", {})
            if not isinstance(legacy_entries, dict) or not isinstance(
                previous_directories, dict
            ):
                raise SyncError("The previous library manifest is invalid")
            if legacy_entries or previous_directories:
                index.import_manifest(legacy_entries, previous_directories)
            del previous_manifest
            del legacy_entries
        else:
            previous_directories = index.directory_entries()
        incremental_state_valid = (
            not index.needs_migration
            and bool(previous_directories)
            and all(
                isinstance(directory, dict)
                and (
                    directory.get("modified") is None
                    or isinstance(directory.get("modified"), str)
                )
                and (
                    directory.get("parent_url") is None
                    or isinstance(directory.get("parent_url"), str)
                )
                for directory in previous_directories.values()
            )
        )
        last_full_crawl = state.get("last_full_crawl")
        full_crawl = (
            not incremental_state_valid
            or migration_requires_full
            or not isinstance(last_full_crawl, (int, float))
            or time.time() - last_full_crawl >= FULL_CRAWL_INTERVAL
        )
        if not incremental_state_valid:
            previous_directories = {}

        (
            categories,
            media_items,
            page_count,
            errors,
            directory_states,
            visited_directories,
        ) = crawl_catalog(
            client,
            client.origin_url,
            cancelled=cancelled,
            progress=progress,
            previous_directories=previous_directories,
            full_crawl=full_crawl,
            media_types=media_types,
        )
        if errors:
            state["last_error"] = "; ".join(errors[:20])
            state["last_result"] = {
                "complete": False,
                "page_count": page_count,
                "media_count": len(media_items),
            }
            _save_json(state_path, state)
            return SyncResult(False, 0, 0, page_count, errors, 0)

        removed_directories = previous_directories.keys() - directory_states.keys()
        affected_directories = set(visited_directories) | removed_directories
        previous_media_count = sum(index.counts())
        if full_crawl:
            old_entries = {}
            old_paths = {_native_relative_path(path) for path in index.media_paths()}
            affected_media_count = previous_media_count
        else:
            old_entries = index.media_entries_for_directories(affected_directories)
            old_entries.update(
                index.media_entries_for_urls(media.url for media in media_items)
            )
            old_paths = {
                _native_relative_path(entry["path"])
                for entry in old_entries.values()
                if isinstance(entry, dict) and isinstance(entry.get("path"), str)
            }
            affected_media_count = len(old_entries)
        if previous_media_count - affected_media_count + len(media_items) <= 0:
            message = (
                "The crawl found no supported videos; "
                "existing library entries were kept"
            )
            state["last_error"] = message
            state["last_result"] = {
                "complete": False,
                "page_count": page_count,
                "media_count": 0,
            }
            _save_json(state_path, state)
            return SyncResult(False, 0, 0, page_count, (message,), 0)

        if not full_crawl:
            index.exclude_media_urls(old_entries)
        used_paths = set()
        desired = {}
        replacements = []
        for media in media_items:
            if cancelled():
                raise SyncCancelled("Sync cancelled because Kodi is shutting down")
            relative_path, media_type = _media_output_path(media)
            relative_path = _limit_output_path(library_dir, relative_path)
            path_key = _path_key(relative_path)
            existing_url = index.media_url_for_path_key(path_key)
            if path_key in used_paths or (
                existing_url is not None
                and existing_url != media.url
                and index.path_key_in_use(path_key)
            ):
                relative_path, media_type = _media_output_path(
                    media,
                    disambiguate=True,
                )
                relative_path = _limit_output_path(library_dir, relative_path)
            path_key = _path_key(relative_path)
            existing_url = index.media_url_for_path_key(path_key)
            if path_key in used_paths or (
                existing_url is not None
                and existing_url != media.url
                and index.path_key_in_use(path_key)
            ):
                raise SyncError(
                    "A generated library path collides with an existing media entry"
                )
            used_paths.add(path_key)
            desired[media.url] = {
                "path": relative_path,
                "media_type": media_type,
                "source_directory": media.source_directory,
            }
            output_path = _managed_path(library_dir, relative_path)
            expected_content = (media.url + "\n").encode("utf-8")
            try:
                with open(output_path, "rb") as existing:
                    unchanged = existing.read() == expected_content
            except FileNotFoundError:
                unchanged = False
            if not unchanged:
                replacements.append(("replace", output_path, media.url + "\n"))
        if cancelled():
            raise SyncCancelled("Sync cancelled because Kodi is shutting down")

        stale_paths = []
        for stale_path in sorted(old_paths):
            if _path_key(stale_path) in used_paths:
                continue
            stale_paths.append(_managed_path(library_dir, stale_path))

        pruned_count = sum(os.path.isfile(path) for path in stale_paths)
        if (
            previous_media_count >= DELETION_CONFIRMATION_MIN_MEDIA
            and len(stale_paths) / previous_media_count >= DELETION_CONFIRMATION_RATIO
        ):
            signature = hashlib.sha256(
                "\n".join(sorted(os.path.relpath(path, library_dir) for path in stale_paths)).encode("utf-8")
            ).hexdigest()
            candidate = state.get("prune_candidate")
            now = time.time()
            first_seen = candidate.get("first_seen") if isinstance(candidate, dict) else None
            valid_first_seen = (
                isinstance(first_seen, (int, float)) and 0 <= first_seen <= now
            )
            confirmed = (
                isinstance(candidate, dict)
                and candidate.get("signature") == signature
                and valid_first_seen
                and now - first_seen >= DELETION_CONFIRMATION_INTERVAL
            )
            if not confirmed:
                if (
                    not isinstance(candidate, dict)
                    or candidate.get("signature") != signature
                    or not valid_first_seen
                ):
                    state["prune_candidate"] = {
                        "signature": signature,
                        "first_seen": now,
                        "count": len(stale_paths),
                        "total": previous_media_count,
                    }
                message = (
                    f"The crawl proposes removing {len(stale_paths)} of "
                    f"{previous_media_count} indexed videos. The same complete "
                    "result must be confirmed after 30 minutes before removal."
                )
                state["last_error"] = message
                state["last_result"] = {
                    "complete": False,
                    "page_count": page_count,
                    "media_count": len(media_items),
                    "candidate_pruned_count": len(stale_paths),
                }
                _save_json(state_path, state)
                return SyncResult(False, 0, 0, page_count, (message,), 0)
        else:
            state.pop("prune_candidate", None)

        state.pop("prune_candidate", None)
        _commit_transaction(
            library_dir,
            index,
            replacements,
            stale_paths,
            {
                "entries": desired,
                "directories": directory_states,
                "previous_directories": previous_directories,
                "full_crawl": full_crawl,
                "visited": visited_directories,
            },
        )
        managed_roots = {
            os.path.realpath(os.path.join(library_dir, "Movies")),
            os.path.realpath(os.path.join(library_dir, "TV Shows")),
        }
        empty_directories = set()
        for stale_path in stale_paths:
            directory = os.path.dirname(stale_path)
            for root in managed_roots:
                try:
                    if os.path.commonpath((root, directory)) != root:
                        continue
                except ValueError:
                    continue
                while directory != root:
                    empty_directories.add(directory)
                    parent = os.path.dirname(directory)
                    if parent == directory:
                        break
                    directory = parent
        for directory in sorted(empty_directories, key=len, reverse=True):
            try:
                os.rmdir(directory)
            except FileNotFoundError:
                continue
            except OSError:
                continue

        if os.path.exists(manifest_path):
            try:
                os.remove(manifest_path)
            except OSError:
                pass
        movie_count, episode_count = index.counts()
        completed_at = time.time()
        state.pop("last_error", None)
        state["last_success"] = completed_at
        if full_crawl:
            state["last_full_crawl"] = completed_at
        state["last_result"] = {
            "complete": True,
            "full_crawl": full_crawl,
            "category_count": len(categories),
            "page_count": page_count,
            "movie_count": movie_count,
            "episode_count": episode_count,
            "pruned_count": pruned_count,
        }
        _save_json(state_path, state)
        return SyncResult(
            True,
            movie_count,
            episode_count,
            page_count,
            errors,
            pruned_count,
        )
    except Exception as error:
        state["last_error"] = str(error)
        _save_json(state_path, state)
        raise
    finally:
        if index is not None:
            index.close()
