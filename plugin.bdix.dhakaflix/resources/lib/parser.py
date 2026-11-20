"""HTML directory-listing parser with h5ai and generic-link support."""

import re
from dataclasses import dataclass
from html.parser import HTMLParser
from urllib.parse import urljoin, urlsplit

from resources.lib.urls import normalize_http_url

VIDEO_EXTENSIONS = frozenset(
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
IMAGE_EXTENSIONS = frozenset((".bmp", ".gif", ".jpeg", ".jpg", ".png", ".webp"))
SUBTITLE_EXTENSIONS = frozenset((".ass", ".srt", ".ssa", ".sub", ".vtt"))

_EXTENSION_KINDS = {
    **{ext: "video" for ext in VIDEO_EXTENSIONS},
    **{ext: "image" for ext in IMAGE_EXTENSIONS},
    **{ext: "subtitle" for ext in SUBTITLE_EXTENSIONS},
}
_LISTING_IDS = frozenset(("fallback", "fb", "fb-list", "h5ai"))
_LISTING_CLASSES = frozenset(("fb-i", "fb-n", "fb-d", "fb-s", "fb-list"))
_FAILURE_MARKERS = (
    "access denied",
    "authentication required",
    "captcha",
    "cloudflare",
    "forbidden",
    "maintenance",
    "sign in",
    "temporarily unavailable",
    "too many requests",
)
_SIZE_UNITS = {
    "": 1,
    "b": 1,
    "byte": 1,
    "bytes": 1,
    "k": 1024,
    "kb": 1024,
    "kib": 1024,
    "m": 1024**2,
    "mb": 1024**2,
    "mib": 1024**2,
    "g": 1024**3,
    "gb": 1024**3,
    "gib": 1024**3,
    "t": 1024**4,
    "tb": 1024**4,
    "tib": 1024**4,
}
_SIZE_RE = re.compile(r"^\s*(\d+(?:\.\d+)?)\s*([a-zA-Z]{0,5})\s*$")
_DATE_HMS_RE = re.compile(r"^(\d{4}-\d{2}-\d{2})\s+(\d{2}:\d{2}:\d{2})$")
_DATE_HM_RE = re.compile(r"^(\d{4}-\d{2}-\d{2})\s+(\d{2}:\d{2})$")
_DATE_ONLY_RE = re.compile(r"^(\d{4}-\d{2}-\d{2})$")
_EPISODE_RE = re.compile(
    r"(?i)(?:^|[\s._\-\[(])S(\d{1,2})E(\d{1,3})(?:$|[\s._\-\])])"
)
_BRACKETED_YEAR_RE = re.compile(r"[\[(]((?:19|20)\d{2})[\])]")
_YEAR_RE = re.compile(r"(?:^|[\s._\-\[(])((?:19|20)\d{2})(?:$|[\s._\-\])])")


@dataclass(frozen=True, slots=True)
class Entry:
    name: str
    url: str
    kind: str
    modified: str = ""
    size: int = 0

    def to_dict(self):
        payload = {
            "name": self.name,
            "url": self.url,
            "kind": self.kind,
        }
        if self.modified:
            payload["modified"] = self.modified
        if self.size:
            payload["size"] = self.size
        return payload

    @classmethod
    def from_dict(cls, value):
        if not isinstance(value, dict):
            raise TypeError("Invalid cached listing entry")
        name, url, kind = value.get("name"), value.get("url"), value.get("kind")
        if not isinstance(name, str) or not name.strip():
            raise ValueError("Invalid cached listing name")
        if not isinstance(url, str) or kind not in (
            "folder",
            "video",
            "image",
            "subtitle",
            "other",
        ):
            raise ValueError("Invalid cached listing entry")
        modified = value.get("modified", "")
        if not isinstance(modified, str):
            raise TypeError("Invalid cached listing modification time")
        size = value.get("size", 0)
        if isinstance(size, bool) or not isinstance(size, int) or size < 0:
            raise ValueError("Invalid cached listing size")
        return cls(
            name.strip(),
            normalize_http_url(url),
            kind,
            modified.strip(),
            size,
        )


def classify_file(name):
    dot_index = name.rfind(".")
    if dot_index < 0:
        return "other"
    return _EXTENSION_KINDS.get(name[dot_index:].casefold(), "other")


def parse_size(text):
    """Parse an h5ai size string into integer bytes, returning 0 if unavailable."""
    if not isinstance(text, str):
        return 0
    match = _SIZE_RE.match(text)
    if not match:
        return 0
    number_text, unit_text = match.groups()
    multiplier = _SIZE_UNITS.get(unit_text.casefold())
    if multiplier is None:
        return 0
    try:
        value = float(number_text)
    except ValueError:
        return 0
    if value < 0:
        return 0
    return int(value * multiplier)


def format_size(size):
    """Format a byte count cleanly for Kodi secondary labels."""
    if isinstance(size, bool) or not isinstance(size, int) or size <= 0:
        return ""
    if size < 1024:
        return f"{size} B"
    value = float(size)
    for unit in ("KB", "MB", "GB", "TB"):
        value /= 1024.0
        if value < 1024.0 or unit == "TB":
            if unit in ("KB", "MB"):
                return f"{value:.0f} {unit}" if value >= 10 else f"{value:.1f} {unit}"
            return f"{value:.1f} {unit}"
    return f"{value:.1f} TB"


def format_kodi_date(modified):
    """Normalize an h5ai timestamp string to Kodi's YYYY-MM-DD HH:MM:SS format."""
    value = " ".join((modified or "").split())
    if not value:
        return ""
    match = _DATE_HMS_RE.match(value)
    if match:
        return f"{match.group(1)} {match.group(2)}"
    match = _DATE_HM_RE.match(value)
    if match:
        return f"{match.group(1)} {match.group(2)}:00"
    match = _DATE_ONLY_RE.match(value)
    if match:
        return f"{match.group(1)} 00:00:00"
    return ""


def parse_media_metadata(name):
    """Extract (clean_title, year, season, episode, mediatype) conservatively."""
    raw = (name or "").strip()
    if not raw:
        return "", None, None, None, "video"
    dot_index = raw.rfind(".")
    if dot_index > 0 and classify_file(raw) != "other":
        stem = raw[:dot_index].strip() or raw
    else:
        stem = raw
    clean_title = " ".join(stem.split())

    season = None
    episode = None
    episode_match = _EPISODE_RE.search(stem)
    if episode_match:
        season = int(episode_match.group(1))
        episode = int(episode_match.group(2))

    year = None
    bracketed = list(_BRACKETED_YEAR_RE.finditer(stem))
    if bracketed:
        candidate = int(bracketed[-1].group(1))
        if stem.strip("()[] ") != str(candidate):
            year = candidate
    else:
        years = list(_YEAR_RE.finditer(stem))
        if years:
            candidate = int(years[-1].group(1))
            if stem != str(candidate):
                year = candidate

    if episode is not None:
        mediatype = "episode"
    elif year is not None:
        mediatype = "movie"
    else:
        mediatype = "video"
    return clean_title, year, season, episode, mediatype


class _H5AIParser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.rows = []
        self.has_listing_marker = False
        self.links = []
        self.page_text = []
        self._row = None
        self._cell = None
        self._anchor = None
        self._ignored_depth = 0

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style"):
            self._ignored_depth += 1
            return
        if self._ignored_depth:
            return

        need_markers = not self.has_listing_marker
        need_attrs = need_markers or tag in ("td", "img", "a")
        class_value = ""
        id_value = ""
        src_value = ""
        href_value = ""
        if need_attrs and attrs:
            for key, value in attrs:
                if value is None:
                    continue
                if key == "class":
                    class_value = value
                elif key == "id" and need_markers:
                    id_value = value
                elif key == "src" and tag == "img":
                    src_value = value
                elif key == "href" and tag == "a":
                    href_value = value

        if need_markers:
            if id_value.casefold() in _LISTING_IDS or any(
                value in _LISTING_CLASSES for value in class_value.casefold().split()
            ):
                self.has_listing_marker = True

        if tag == "tr":
            self._row = {
                "icon": "",
                "href": "",
                "name": "",
                "modified": [],
                "size": [],
            }
        elif self._row is not None and tag == "td":
            classes = class_value.split()
            self._cell = classes[0] if classes else ""
        elif self._row is not None and tag == "img" and self._cell == "fb-i":
            self._row["icon"] = src_value
        elif tag == "a":
            self._anchor = {
                "href": href_value,
                "text": [],
                "is_h5ai_name": self._row is not None and self._cell == "fb-n",
            }

    def handle_data(self, data):
        if self._ignored_depth:
            return
        if self._row is None and self._anchor is None:
            stripped = data.strip()
            if stripped:
                self.page_text.append(stripped)
        if self._anchor is not None:
            self._anchor["text"].append(data)
        if self._row is not None:
            if self._cell == "fb-d":
                self._row["modified"].append(data)
            elif self._cell == "fb-s":
                self._row["size"].append(data)

    def handle_endtag(self, tag):
        if tag in ("script", "style"):
            self._ignored_depth = max(0, self._ignored_depth - 1)
            return
        if self._ignored_depth:
            return
        if tag == "a" and self._anchor is not None:
            link = self._anchor
            text = " ".join("".join(link["text"]).split())
            if not self.rows:
                self.links.append((link["href"], text))
            if link["is_h5ai_name"] and self._row is not None:
                self._row["href"] = link["href"]
                self._row["name"] = text
            self._anchor = None
        elif tag == "td":
            self._cell = None
        elif tag == "tr":
            if self._row is not None and self._row["href"] and self._row["name"]:
                if not self.rows:
                    self.links.clear()
                self.rows.append(self._row)
            self._row = None
            self._cell = None


def _is_parent(name, href):
    return name.casefold() in ("parent directory", "..", "up") or href.strip() in (
        "..",
        "../",
    )


def _resolve(base_url, href):
    href = (href or "").strip()
    if not href or href == "//" or href.startswith("#"):
        return ""
    try:
        return normalize_http_url(urljoin(base_url, href))
    except ValueError:
        return ""


def _entry(name, href, base_url, icon="", modified="", size=0):
    name = " ".join((name or "").split())
    if not name or _is_parent(name, href):
        return None
    resolved = _resolve(base_url, href)
    if not resolved:
        return None
    if "folder" in icon.casefold() or href.rstrip().endswith("/"):
        kind = "folder"
        size = 0
    else:
        kind = classify_file(name)
        if kind == "other":
            linked_name = urlsplit(resolved).path.rsplit("/", 1)[-1]
            linked_kind = classify_file(linked_name)
            if linked_kind != "other":
                kind = linked_kind
    return Entry(name=name, url=resolved, kind=kind, modified=modified, size=size)


class DirectoryStreamParser:
    """Streaming HTML directory parser for chunked HTTP responses."""

    def __init__(self, base_url, excluded_keywords=(), strict=False):
        self.base_url = base_url
        self.excluded_keywords = tuple(
            word.casefold() for word in excluded_keywords if word
        )
        self.strict = strict
        self._parser = _H5AIParser()

    def feed(self, chunk):
        if chunk:
            self._parser.feed(chunk)

    def finalize(self):
        self._parser.close()
        parser = self._parser
        entries = []
        seen = set()
        if parser.rows:
            candidates = [
                _entry(
                    row["name"],
                    row["href"],
                    self.base_url,
                    row["icon"],
                    " ".join("".join(row["modified"]).split()),
                    parse_size("".join(row["size"]).strip()),
                )
                for row in parser.rows
            ]
        else:
            candidates = [
                _entry(name, href, self.base_url)
                for href, name in parser.links
                if name
            ]

        if self.strict:
            page_text = " ".join(parser.page_text).casefold()
            if any(marker in page_text for marker in _FAILURE_MARKERS):
                raise ValueError(
                    "The response looks like an access, challenge, or error page"
                )
            if (
                not parser.rows
                and not any(candidates)
                and not parser.has_listing_marker
            ):
                raise ValueError(
                    "The response does not contain a recognized directory listing"
                )

        for entry in candidates:
            if entry is None or entry.url in seen:
                continue
            if entry.kind == "folder" and any(
                word in (entry.name + " " + entry.url).casefold()
                for word in self.excluded_keywords
            ):
                continue
            seen.add(entry.url)
            entries.append(entry)

        entries.sort(
            key=lambda entry: (
                entry.kind != "folder",
                entry.name.casefold(),
                entry.url,
            )
        )
        return entries


def parse_directory(html_text, base_url, excluded_keywords=(), strict=False):
    stream = DirectoryStreamParser(
        base_url,
        excluded_keywords=excluded_keywords,
        strict=strict,
    )
    stream.feed(html_text)
    return stream.finalize()


__all__ = [
    "DirectoryStreamParser",
    "Entry",
    "IMAGE_EXTENSIONS",
    "SUBTITLE_EXTENSIONS",
    "VIDEO_EXTENSIONS",
    "classify_file",
    "format_kodi_date",
    "format_size",
    "parse_directory",
    "parse_media_metadata",
    "parse_size",
]
