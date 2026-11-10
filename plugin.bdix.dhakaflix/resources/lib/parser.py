"""HTML directory-listing parser with h5ai and generic-link support."""

from dataclasses import asdict, dataclass
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


@dataclass(frozen=True, slots=True)
class Entry:
    name: str
    url: str
    kind: str
    modified: str = ""

    def to_dict(self):
        return asdict(self)

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
        return cls(name.strip(), normalize_http_url(url), kind, modified.strip())


def classify_file(name):
    lowered = name.casefold()
    for extensions, kind in (
        (VIDEO_EXTENSIONS, "video"),
        (IMAGE_EXTENSIONS, "image"),
        (SUBTITLE_EXTENSIONS, "subtitle"),
    ):
        if any(lowered.endswith(extension) for extension in extensions):
            return kind
    return "other"


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

    def handle_starttag(self, tag, attrs):
        attributes = dict(attrs)
        classes = attributes.get("class", "").casefold().split()
        identifier = attributes.get("id", "").casefold()
        if identifier in ("fallback", "fb", "fb-list", "h5ai") or any(
            value in ("fb-i", "fb-n", "fb-d", "fb-s", "fb-list") for value in classes
        ):
            self.has_listing_marker = True
        if tag == "tr":
            self._row = {"icon": "", "href": "", "name": "", "modified": []}
        elif self._row is not None and tag == "td":
            classes = attributes.get("class", "").split()
            self._cell = classes[0] if classes else ""
        elif self._row is not None and tag == "img" and self._cell == "fb-i":
            self._row["icon"] = attributes.get("src", "")
        elif tag == "a":
            self._anchor = {
                "href": attributes.get("href", ""),
                "text": [],
                "is_h5ai_name": self._row is not None and self._cell == "fb-n",
            }

    def handle_data(self, data):
        if self._row is None and self._anchor is None:
            self.page_text.append(data)
        if self._anchor is not None:
            self._anchor["text"].append(data)
        if self._row is not None and self._cell == "fb-d":
            self._row["modified"].append(data)

    def handle_endtag(self, tag):
        if tag == "a" and self._anchor is not None:
            link = self._anchor
            text = " ".join("".join(link["text"]).split())
            self.links.append((link["href"], text))
            if link["is_h5ai_name"] and self._row is not None:
                self._row["href"] = link["href"]
                self._row["name"] = text
            self._anchor = None
        elif tag == "td":
            self._cell = None
        elif tag == "tr":
            if self._row is not None and self._row["href"] and self._row["name"]:
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


def _entry(name, href, base_url, icon="", modified=""):
    name = " ".join((name or "").split())
    if not name or _is_parent(name, href):
        return None
    resolved = _resolve(base_url, href)
    if not resolved:
        return None
    if "folder" in icon.casefold() or href.rstrip().endswith("/"):
        kind = "folder"
    else:
        kind = classify_file(name)
        if kind == "other":
            linked_name = urlsplit(resolved).path.rsplit("/", 1)[-1]
            linked_kind = classify_file(linked_name)
            if linked_kind != "other":
                kind = linked_kind
    return Entry(name=name, url=resolved, kind=kind, modified=modified)


def parse_directory(html_text, base_url, excluded_keywords=(), strict=False):
    parser = _H5AIParser()
    parser.feed(html_text)
    parser.close()

    entries = []
    seen = set()
    if parser.rows:
        candidates = [
            _entry(
                row["name"],
                row["href"],
                base_url,
                row["icon"],
                " ".join(row["modified"]).strip(),
            )
            for row in parser.rows
        ]
    else:
        candidates = [
            _entry(name, href, base_url) for href, name in parser.links if name
        ]

    if strict:
        page_text = " ".join(parser.page_text).casefold()
        failure_markers = (
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
        if any(marker in page_text for marker in failure_markers):
            raise ValueError(
                "The response looks like an access, challenge, or error page"
            )
        if not parser.rows and not any(candidates) and not parser.has_listing_marker:
            raise ValueError(
                "The response does not contain a recognized directory listing"
            )
    excluded = tuple(word.casefold() for word in excluded_keywords if word)
    for entry in candidates:
        if entry is None or entry.url in seen:
            continue
        if entry.kind == "folder" and any(
            word in (entry.name + " " + entry.url).casefold() for word in excluded
        ):
            continue
        seen.add(entry.url)
        entries.append(entry)

    entries.sort(
        key=lambda entry: (entry.kind != "folder", entry.name.casefold(), entry.url)
    )
    return entries
