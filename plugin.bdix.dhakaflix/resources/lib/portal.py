"""HTTP access and URL validation for portal directory listings."""

import json
from urllib.error import HTTPError, URLError
from urllib.parse import quote, unquote, urljoin, urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

from resources.lib.cache import CacheError
from resources.lib.parser import Entry, classify_file, parse_directory
from resources.lib.urls import URLValidationError, normalize_http_url

MAX_PAGE_BYTES = 8 * 1024 * 1024
MAX_SEARCH_BYTES = 4 * 1024 * 1024


class PortalError(RuntimeError):
    """Raised when a portal page cannot be fetched or parsed."""


class _ValidatedRedirectHandler(HTTPRedirectHandler):
    def redirect_request(self, request, response, code, message, headers, new_url):
        try:
            target = normalize_http_url(urljoin(request.full_url, new_url))
        except URLValidationError as error:
            raise HTTPError(
                request.full_url,
                code,
                "Refused unsafe redirect: {}".format(error),
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
        log_warning=None,
    ):
        try:
            self.origin_url = normalize_http_url(origin_url)
        except URLValidationError as error:
            raise PortalError("Invalid portal URL: {}".format(error)) from error
        self.excluded_keywords = tuple(excluded_keywords)
        self.timeout = min(max(int(timeout), 3), 60)
        self.user_agent = user_agent
        self.log_warning = log_warning or (lambda message: None)
        self.opener = build_opener(_ValidatedRedirectHandler())

    def search_videos(self, server_url, server_name, query):
        try:
            base_url = normalize_http_url(server_url)
        except URLValidationError as error:
            raise PortalError("Invalid search server URL: {}".format(error)) from error
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
        try:
            with self.opener.open(request, timeout=self.timeout) as response:
                if response.status < 200 or response.status >= 300:
                    raise PortalError(
                        "Search server returned HTTP {} for {}".format(
                            response.status, endpoint
                        )
                    )
                try:
                    final_url = normalize_http_url(response.geturl())
                except URLValidationError as error:
                    raise PortalError("Search server redirected to an invalid URL") from error
                if _origin(final_url) != _origin(base_url):
                    raise PortalError("Search server redirected to a different host")
                raw_content = response.read(MAX_SEARCH_BYTES + 1)
                if len(raw_content) > MAX_SEARCH_BYTES:
                    raise PortalError("Search response exceeded the 4 MiB size limit")
        except HTTPError as error:
            error.close()
            raise PortalError(
                "Search server returned HTTP {} for {}".format(error.code, endpoint)
            ) from error
        except (URLError, TimeoutError, OSError) as error:
            raise PortalError(
                "Unable to search {}: {}".format(endpoint, error)
            ) from error

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
                target_url = normalize_http_url(urljoin(base_url.rstrip("/") + "/", href))
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
            raise PortalError("Invalid directory URL: {}".format(error)) from error

        if not refresh:
            try:
                cached = cache.get(target_url)
            except CacheError as error:
                self.log_warning("Ignoring unreadable listing cache: {}".format(error))
                cached = None
            if cached is not None:
                return cached

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
                        "Portal returned HTTP {} for {}".format(response.status, target_url)
                    )
                try:
                    normalize_http_url(response.geturl())
                except URLValidationError as error:
                    raise PortalError("Portal redirected to an invalid URL: {}".format(error)) from error
                content_type = response.headers.get_content_type()
                if content_type not in ("text/html", "application/xhtml+xml"):
                    raise PortalError(
                        "Expected an HTML directory page, received {}".format(content_type)
                    )
                raw_content = response.read(MAX_PAGE_BYTES + 1)
                if len(raw_content) > MAX_PAGE_BYTES:
                    raise PortalError("Directory page exceeded the 8 MiB size limit")
                charset = response.headers.get_content_charset() or "utf-8"
        except HTTPError as error:
            error.close()
            raise PortalError(
                "Portal returned HTTP {} for {}".format(error.code, target_url)
            ) from error
        except (URLError, TimeoutError, OSError) as error:
            raise PortalError("Unable to fetch {}: {}".format(target_url, error)) from error
        except LookupError as error:
            raise PortalError("Portal declared an unsupported text encoding") from error

        try:
            html_text = raw_content.decode(charset, errors="replace")
        except LookupError as error:
            raise PortalError("Portal declared an unsupported text encoding") from error

        entries = parse_directory(
            html_text,
            target_url,
            excluded_keywords=self.excluded_keywords,
        )
        try:
            cache.put(target_url, entries)
        except CacheError as error:
            self.log_warning("Listing fetched but could not be cached: {}".format(error))
        return entries


def _origin(url):
    parts = urlsplit(url)
    return parts.scheme.casefold(), parts.hostname.casefold(), parts.port
