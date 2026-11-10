"""HTTP access and URL validation for portal directory listings."""

import json
from urllib.error import HTTPError, URLError
from urllib.parse import quote, unquote, urljoin, urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

from resources.lib.cache import CacheError
from resources.lib.config import parse_integer_setting
from resources.lib.parser import Entry, classify_file, parse_directory
from resources.lib.urls import URLValidationError, http_origin, normalize_http_url

MAX_SEARCH_BYTES = 4 * 1024 * 1024


class PortalError(RuntimeError):
    """Raised when a portal page cannot be fetched or parsed."""


class _ValidatedRedirectHandler(HTTPRedirectHandler):
    def __init__(self, allowed_origins, same_origin_only=False):
        super().__init__()
        self.allowed_origins = frozenset(allowed_origins)
        self.same_origin_only = same_origin_only

    def redirect_request(self, request, response, code, message, headers, new_url):
        try:
            target = normalize_http_url(urljoin(request.full_url, new_url))
            target_origin = http_origin(target)
            request_origin = http_origin(request.full_url)
            if target_origin not in self.allowed_origins:
                raise URLValidationError(
                    "redirect destination is not an approved server"
                )
            if self.same_origin_only and target_origin != request_origin:
                raise URLValidationError("cross-origin redirects are not allowed")
        except URLValidationError as error:
            raise HTTPError(
                request.full_url,
                code,
                f"Refused unsafe redirect: {error}",
                headers,
                response,
            ) from error
        return super().redirect_request(
            request, response, code, message, headers, target
        )


class PortalClient:
    def __init__(
        self,
        origin_url,
        excluded_keywords=(),
        timeout=20,
        user_agent="Kodi DhakaFlix",
        warning_callback=None,
        allowed_origins=None,
    ):
        try:
            self.origin_url = normalize_http_url(origin_url)
        except URLValidationError as error:
            raise PortalError(f"Invalid portal URL: {error}") from error
        configured_origins = {http_origin(self.origin_url)}
        for allowed_url in allowed_origins or ():
            if not allowed_url:
                continue
            try:
                configured_origins.add(http_origin(normalize_http_url(allowed_url)))
            except URLValidationError as error:
                raise PortalError(f"Invalid approved server URL: {error}") from error
        self.allowed_origins = frozenset(configured_origins)
        self.excluded_keywords = tuple(excluded_keywords)
        self.timeout = parse_integer_setting(
            timeout,
            name="Request timeout",
            default=20,
            minimum=3,
            maximum=60,
        )
        self.user_agent = user_agent
        self.warning_callback = warning_callback or (lambda message: None)
        self.opener = build_opener(_ValidatedRedirectHandler(self.allowed_origins))

    def fetch_html(self, url):
        try:
            target_url = normalize_http_url(url)
        except URLValidationError as error:
            raise PortalError(f"Invalid directory URL: {error}") from error

        if http_origin(target_url) not in self.allowed_origins:
            raise PortalError("Directory URL is not on an approved DhakaFlix server")

        request = Request(
            target_url,
            headers={
                "Accept": "text/html,application/xhtml+xml",
                "User-Agent": self.user_agent,
            },
        )
        try:
            with self.opener.open(request, timeout=self.timeout) as response:
                if response.status < 200 or response.status >= 300:
                    raise PortalError(
                        f"Portal returned HTTP {response.status} for {target_url}"
                    )
                try:
                    final_url = normalize_http_url(response.geturl())
                except URLValidationError as error:
                    raise PortalError(
                        f"Portal redirected to an invalid URL: {error}"
                    ) from error
                if http_origin(final_url) not in self.allowed_origins:
                    raise PortalError("Portal redirected to an unapproved server")
                content_type = response.headers.get_content_type()
                if content_type not in ("text/html", "application/xhtml+xml"):
                    raise PortalError(
                        f"Expected an HTML directory page, received {content_type}"
                    )
                raw_content = response.read()
                charset = response.headers.get_content_charset() or "utf-8"
        except HTTPError as error:
            error.close()
            raise PortalError(
                f"Portal returned HTTP {error.code} for {target_url}"
            ) from error
        except (URLError, TimeoutError, OSError) as error:
            raise PortalError(f"Unable to fetch {target_url}: {error}") from error
        except LookupError as error:
            raise PortalError("Portal declared an unsupported text encoding") from error

        try:
            return raw_content.decode(charset, errors="replace"), final_url
        except LookupError as error:
            raise PortalError("Portal declared an unsupported text encoding") from error

    def search_videos(self, server_url, server_name, query):
        try:
            base_url = normalize_http_url(server_url)
        except URLValidationError as error:
            raise PortalError(f"Invalid search server URL: {error}") from error
        if http_origin(base_url) not in self.allowed_origins:
            raise PortalError("Search server is not an approved DhakaFlix server")
        query = " ".join(query.split())
        if not query:
            raise PortalError("Search text cannot be empty")
        if len(query) > 200:
            raise PortalError("Search text cannot exceed 200 characters")

        endpoint = urljoin(
            base_url.rstrip("/") + "/",
            quote(server_name, safe="") + "/",
        )
        body = json.dumps(
            {
                "action": "get",
                "search": {
                    "href": "/" + server_name + "/",
                    "pattern": query,
                    "ignorecase": True,
                },
            }
        ).encode("utf-8")
        request = Request(
            endpoint,
            data=body,
            headers={
                "Accept": "application/json",
                "Content-Type": "application/json",
                "User-Agent": self.user_agent,
            },
            method="POST",
        )
        search_opener = build_opener(
            _ValidatedRedirectHandler(self.allowed_origins, same_origin_only=True)
        )
        try:
            with search_opener.open(request, timeout=self.timeout) as response:
                if response.status < 200 or response.status >= 300:
                    raise PortalError(
                        f"Search server returned HTTP {response.status} for {endpoint}"
                    )
                try:
                    final_url = normalize_http_url(response.geturl())
                except URLValidationError as error:
                    raise PortalError(
                        "Search server redirected to an invalid URL"
                    ) from error
                if _origin(final_url) != _origin(base_url):
                    raise PortalError("Search server redirected to a different host")
                raw_content = response.read(MAX_SEARCH_BYTES + 1)
                if len(raw_content) > MAX_SEARCH_BYTES:
                    raise PortalError("Search response exceeded the 4 MiB size limit")
        except HTTPError as error:
            error.close()
            raise PortalError(
                f"Search server returned HTTP {error.code} for {endpoint}"
            ) from error
        except (URLError, TimeoutError, OSError) as error:
            raise PortalError(f"Unable to search {endpoint}: {error}") from error

        try:
            payload = json.loads(raw_content.decode("utf-8"))
        except (UnicodeDecodeError, ValueError) as error:
            raise PortalError("Search server returned invalid JSON") from error
        results = payload.get("search") if isinstance(payload, dict) else None
        if not isinstance(results, list):
            raise PortalError("Search server response has no result list")

        entries = []
        seen = set()
        for result in results:
            if not isinstance(result, dict):
                continue
            href = result.get("href")
            if not isinstance(href, str) or not href.strip():
                continue
            try:
                target_url = normalize_http_url(
                    urljoin(base_url.rstrip("/") + "/", href)
                )
            except URLValidationError:
                continue
            if _origin(target_url) != _origin(base_url):
                continue
            name = unquote(urlsplit(target_url).path.rsplit("/", 1)[-1]).strip()
            if classify_file(name) != "video" or target_url in seen:
                continue
            seen.add(target_url)
            entries.append(Entry(name=name, url=target_url, kind="video"))
        return entries

    def listing(self, url, cache, refresh=False):
        try:
            target_url = normalize_http_url(url)
        except URLValidationError as error:
            raise PortalError(f"Invalid directory URL: {error}") from error

        if cache is not None and not refresh:
            try:
                cached = cache.get(target_url)
            except CacheError as error:
                self.warning_callback(f"Ignoring unreadable listing cache: {error}")
                cached = None
            if cached is not None:
                return cached

        html_text, final_url = self.fetch_html(target_url)

        try:
            entries = parse_directory(
                html_text,
                final_url,
                excluded_keywords=self.excluded_keywords,
                strict=True,
            )
        except ValueError as error:
            raise PortalError(
                f"Portal returned an invalid directory listing: {error}"
            ) from error
        entries = [
            entry for entry in entries if http_origin(entry.url) in self.allowed_origins
        ]
        if cache is not None:
            try:
                cache.put(target_url, entries)
            except CacheError as error:
                self.warning_callback(
                    f"Listing fetched but could not be cached: {error}"
                )
        return entries


def _origin(url):
    return http_origin(url)
