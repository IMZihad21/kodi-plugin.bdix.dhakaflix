"""Safe library output paths and media naming."""

import hashlib
import os
import re
import unicodedata
from urllib.parse import unquote, urlsplit

from .models import SyncError

MAX_LIBRARY_PATH_LENGTH = 240
MAX_COMPONENT_BYTES = 180

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


def _managed_path(root_dir, relative_path):
    root = os.path.realpath(root_dir)
    path = os.path.realpath(os.path.join(root, relative_path))
    if os.path.commonpath((root, path)) != root or not path.lower().endswith(".strm"):
        raise SyncError("Refusing to access a path outside the managed library")
    return path
