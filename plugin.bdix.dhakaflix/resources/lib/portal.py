"""HTTP access with keep-alive pooling, compression, and URL validation."""

import codecs
import http.client
import json
import threading
import time
import zlib
from contextlib import suppress
from datetime import UTC, datetime
from urllib.parse import quote, unquote, urljoin, urlsplit

from resources.lib.cache import CacheError
from resources.lib.config import parse_integer_setting
from resources.lib.parser import (
    DirectoryStreamParser,
    Entry,
    classify_file,
    parse_size,
)
from resources.lib.urls import URLValidationError, http_origin, normalize_http_url

MAX_SEARCH_BYTES = 4 * 1024 * 1024
_POOL_MAX_PER_ORIGIN = 4
_POOL_IDLE_SECONDS = 30.0
_MAX_REDIRECTS = 5
_CHUNK_SIZE = 65536
_STALE_SOCKET_ERRORS = (
    http.client.RemoteDisconnected,
    http.client.BadStatusLine,
    ConnectionResetError,
    BrokenPipeError,
)


class PortalError(RuntimeError):
    """Raised when a portal page cannot be fetched or parsed."""


class _ConnectionPool:
    """Thread-safe per-origin HTTP/HTTPS keep-alive connection pool."""

    def __init__(self):
        self._lock = threading.Lock()
        self._idle: dict[
            tuple[str, str, int | None],
            list[tuple[http.client.HTTPConnection, float]],
        ] = {}
        self._closed = False

    def acquire(self, origin, timeout):
        now = time.monotonic()
        stale = []
        reused_conn = None
        with self._lock:
            if self._closed:
                raise PortalError("Portal HTTP client is closed")
            bucket = self._idle.get(origin)
            if bucket:
                while bucket:
                    conn, last_used = bucket.pop()
                    if now - last_used <= _POOL_IDLE_SECONDS:
                        reused_conn = conn
                        break
                    stale.append(conn)
        for conn in stale:
            with suppress(Exception):
                conn.close()
        if reused_conn is not None:
            reused_conn.timeout = timeout
            if reused_conn.sock is not None:
                with suppress(OSError):
                    reused_conn.sock.settimeout(timeout)
            return reused_conn, True

        scheme, host, port = origin
        if scheme == "https":
            return http.client.HTTPSConnection(host, port, timeout=timeout), False
        return http.client.HTTPConnection(host, port, timeout=timeout), False

    def release(self, origin, conn):
        now = time.monotonic()
        to_close = []
        with self._lock:
            if self._closed:
                to_close.append(conn)
            else:
                bucket = self._idle.setdefault(origin, [])
                fresh = []
                for existing, last_used in bucket:
                    if now - last_used <= _POOL_IDLE_SECONDS:
                        fresh.append((existing, last_used))
                    else:
                        to_close.append(existing)
                while len(fresh) >= _POOL_MAX_PER_ORIGIN:
                    oldest, _ = fresh.pop(0)
                    to_close.append(oldest)
                fresh.append((conn, now))
                self._idle[origin] = fresh
        for stale_conn in to_close:
            with suppress(Exception):
                stale_conn.close()

    def close(self):
        with self._lock:
            self._closed = True
            buckets = list(self._idle.values())
            self._idle.clear()
        for bucket in buckets:
            for conn, _ in bucket:
                with suppress(Exception):
                    conn.close()


def _finish_connection(pool, origin, conn, response, reusable):
    if (
        reusable
        and response is not None
        and not response.will_close
        and response.isclosed()
    ):
        pool.release(origin, conn)
        return
    if response is not None:
        with suppress(Exception):
            response.close()
    with suppress(Exception):
        conn.close()


def _iter_decompressed(
    response,
    max_bytes=None,
    limit_message="Response exceeded limit",
):
    encoding = (response.getheader("Content-Encoding") or "").strip().casefold()
    if encoding in ("", "identity"):
        decompressor = None
        raw_fallback = False
    elif encoding == "gzip":
        decompressor = zlib.decompressobj(16 + zlib.MAX_WBITS)
        raw_fallback = False
    elif encoding == "deflate":
        decompressor = zlib.decompressobj()
        raw_fallback = True
    else:
        raise PortalError(f"Unsupported content encoding: {encoding}")

    total = 0
    while True:
        chunk = response.read(_CHUNK_SIZE)
        if not chunk:
            break
        if decompressor is not None:
            try:
                data = decompressor.decompress(chunk)
            except zlib.error as error:
                if raw_fallback:
                    raw_fallback = False
                    decompressor = zlib.decompressobj(-zlib.MAX_WBITS)
                    try:
                        data = decompressor.decompress(chunk)
                    except zlib.error as inner:
                        raise PortalError(
                            f"Unable to decode compressed response: {inner}"
                        ) from inner
                else:
                    raise PortalError(
                        f"Unable to decode compressed response: {error}"
                    ) from error
            raw_fallback = False
        else:
            data = chunk
        if data:
            total += len(data)
            if max_bytes is not None and total > max_bytes:
                raise PortalError(limit_message)
            yield data

    if decompressor is not None:
        try:
            tail = decompressor.flush()
        except zlib.error as error:
            raise PortalError(
                f"Unable to decode compressed response: {error}"
            ) from error
        if tail:
            total += len(tail)
            if max_bytes is not None and total > max_bytes:
                raise PortalError(limit_message)
            yield tail


def _format_search_time(value):
    if isinstance(value, (int, float)) and not isinstance(value, bool) and value > 0:
        timestamp = value / 1000.0 if value > 10_000_000_000 else float(value)
        return datetime.fromtimestamp(timestamp, tz=UTC).strftime("%Y-%m-%d %H:%M")
    if isinstance(value, str):
        return value.strip()
    return ""


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
        self.excluded_keywords = tuple(
            word.casefold() for word in excluded_keywords if word
        )
        self.timeout = parse_integer_setting(
            timeout,
            name="Request timeout",
            default=20,
            minimum=3,
            maximum=60,
        )
        self.user_agent = user_agent
        self.warning_callback = warning_callback or (lambda message: None)
        self._pool = _ConnectionPool()

    def close(self):
        self._pool.close()

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        self.close()

    def _filter_entries(self, entries):
        filtered = []
        for entry in entries:
            if http_origin(entry.url) not in self.allowed_origins:
                continue
            if (
                self.excluded_keywords
                and entry.kind == "folder"
                and any(
                    word in (entry.name + " " + entry.url).casefold()
                    for word in self.excluded_keywords
                )
            ):
                continue
            filtered.append(entry)
        return filtered

    def _open(
        self,
        method,
        url,
        headers,
        body=None,
        same_origin_only=False,
        error_prefix="Unable to fetch",
    ):
        current_method = method
        current_url = url
        current_body = body
        current_headers = dict(headers)

        for redirect_count in range(_MAX_REDIRECTS + 1):
            origin = http_origin(current_url)
            if origin not in self.allowed_origins:
                raise PortalError(
                    "Directory URL is not on an approved DhakaFlix server"
                )
            parts = urlsplit(current_url)
            request_path = parts.path or "/"
            if parts.query:
                request_path = f"{request_path}?{parts.query}"
            request_headers = {
                "Accept-Encoding": "gzip, deflate",
                "Connection": "keep-alive",
                "User-Agent": self.user_agent,
                **current_headers,
            }

            conn = None
            response = None
            attempt = 0
            while True:
                attempt += 1
                conn, reused = self._pool.acquire(origin, self.timeout)
                try:
                    conn.request(
                        current_method,
                        request_path,
                        body=current_body,
                        headers=request_headers,
                    )
                    response = conn.getresponse()
                    break
                except _STALE_SOCKET_ERRORS as error:
                    with suppress(Exception):
                        conn.close()
                    if reused and attempt == 1:
                        continue
                    raise PortalError(
                        f"{error_prefix} {current_url}: {error}"
                    ) from error
                except (http.client.HTTPException, OSError) as error:
                    with suppress(Exception):
                        conn.close()
                    raise PortalError(
                        f"{error_prefix} {current_url}: {error}"
                    ) from error

            if response.status in (301, 302, 303, 307, 308):
                location = response.getheader("Location")
                reusable = False
                try:
                    response.read()
                    reusable = True
                except (http.client.HTTPException, OSError):
                    reusable = False
                finally:
                    _finish_connection(self._pool, origin, conn, response, reusable)

                if not location:
                    raise PortalError(
                        f"Portal returned redirect HTTP {response.status} "
                        f"without Location for {current_url}"
                    )
                if redirect_count >= _MAX_REDIRECTS:
                    raise PortalError(f"Too many redirects for {url}")
                try:
                    next_url = normalize_http_url(urljoin(current_url, location))
                except URLValidationError as error:
                    raise PortalError(
                        f"Portal redirected to an invalid URL: {error}"
                    ) from error
                next_origin = http_origin(next_url)
                if next_origin not in self.allowed_origins:
                    raise PortalError("Portal redirected to an unapproved server")
                if same_origin_only and next_origin != origin:
                    raise PortalError("Search server redirected to a different host")
                if response.status in (301, 302, 303) and current_method == "POST":
                    current_method = "GET"
                    current_body = None
                    current_headers.pop("Content-Type", None)
                    current_headers.pop("Content-Length", None)
                current_url = next_url
                continue

            return origin, conn, response, current_url

        raise PortalError(f"Too many redirects for {url}")

    def fetch_html(self, url):
        try:
            target_url = normalize_http_url(url)
        except URLValidationError as error:
            raise PortalError(f"Invalid directory URL: {error}") from error

        if http_origin(target_url) not in self.allowed_origins:
            raise PortalError("Directory URL is not on an approved DhakaFlix server")

        origin, conn, response, final_url = self._open(
            "GET",
            target_url,
            {"Accept": "text/html,application/xhtml+xml"},
            error_prefix="Unable to fetch",
        )
        reusable = False
        try:
            if response.status < 200 or response.status >= 300:
                raise PortalError(
                    f"Portal returned HTTP {response.status} for {target_url}"
                )
            content_type = response.headers.get_content_type()
            if content_type not in ("text/html", "application/xhtml+xml"):
                raise PortalError(
                    f"Expected an HTML directory page, received {content_type}"
                )
            charset = response.headers.get_content_charset() or "utf-8"
            try:
                decoder = codecs.getincrementaldecoder(charset)("replace")
            except LookupError as error:
                raise PortalError(
                    "Portal declared an unsupported text encoding"
                ) from error
            chunks = [
                decoder.decode(chunk)
                for chunk in _iter_decompressed(response)
            ]
            chunks.append(decoder.decode(b"", final=True))
            reusable = True
            return "".join(chunks), final_url
        except (http.client.HTTPException, OSError) as error:
            raise PortalError(f"Unable to fetch {target_url}: {error}") from error
        finally:
            _finish_connection(self._pool, origin, conn, response, reusable)

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

        origin, conn, response, final_url = self._open(
            "POST",
            endpoint,
            {
                "Accept": "application/json",
                "Content-Type": "application/json",
                "Content-Length": str(len(body)),
            },
            body=body,
            same_origin_only=True,
            error_prefix="Unable to search",
        )
        reusable = False
        try:
            if response.status < 200 or response.status >= 300:
                raise PortalError(
                    f"Search server returned HTTP {response.status} for {endpoint}"
                )
            if _origin(final_url) != _origin(base_url):
                raise PortalError("Search server redirected to a different host")
            raw_content = b"".join(
                _iter_decompressed(
                    response,
                    max_bytes=MAX_SEARCH_BYTES,
                    limit_message="Search response exceeded the 4 MiB size limit",
                )
            )
            reusable = True
        except (http.client.HTTPException, OSError) as error:
            raise PortalError(f"Unable to search {endpoint}: {error}") from error
        finally:
            _finish_connection(self._pool, origin, conn, response, reusable)

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
            raw_size = result.get("size")
            if (
                isinstance(raw_size, int)
                and not isinstance(raw_size, bool)
                and raw_size >= 0
            ):
                size = raw_size
            elif isinstance(raw_size, str):
                size = parse_size(raw_size)
            else:
                size = 0
            modified = _format_search_time(result.get("time"))
            entries.append(
                Entry(
                    name=name,
                    url=target_url,
                    kind="video",
                    modified=modified,
                    size=size,
                )
            )
        return entries

    def listing(self, url, cache, refresh=False):
        try:
            target_url = normalize_http_url(url)
        except URLValidationError as error:
            raise PortalError(f"Invalid directory URL: {error}") from error

        if http_origin(target_url) not in self.allowed_origins:
            raise PortalError("Directory URL is not on an approved DhakaFlix server")

        stale_entries = None
        etag = ""
        last_modified = ""
        if cache is not None and not refresh:
            try:
                stale_entries, etag, last_modified, is_fresh = cache.get_stale(
                    target_url
                )
            except CacheError as error:
                self.warning_callback(f"Ignoring unreadable listing cache: {error}")
                stale_entries = None
                etag = ""
                last_modified = ""
                is_fresh = False
            if is_fresh and stale_entries is not None:
                return self._filter_entries(stale_entries)

        request_headers = {"Accept": "text/html,application/xhtml+xml"}
        if not refresh and stale_entries is not None:
            if etag:
                request_headers["If-None-Match"] = etag
            if last_modified:
                request_headers["If-Modified-Since"] = last_modified

        origin, conn, response, final_url = self._open(
            "GET",
            target_url,
            request_headers,
            error_prefix="Unable to fetch",
        )
        reusable = False
        try:
            if response.status == 304 and stale_entries is not None:
                response.read()
                new_etag = response.getheader("ETag")
                new_last_modified = response.getheader("Last-Modified")
                reusable = True
                if cache is not None:
                    try:
                        cache.touch(
                            target_url,
                            etag=new_etag,
                            last_modified=new_last_modified,
                        )
                    except CacheError as error:
                        self.warning_callback(
                            f"Listing revalidated but cache could not be updated: "
                            f"{error}"
                        )
                return self._filter_entries(stale_entries)

            if response.status < 200 or response.status >= 300:
                raise PortalError(
                    f"Portal returned HTTP {response.status} for {target_url}"
                )
            content_type = response.headers.get_content_type()
            if content_type not in ("text/html", "application/xhtml+xml"):
                raise PortalError(
                    f"Expected an HTML directory page, received {content_type}"
                )
            charset = response.headers.get_content_charset() or "utf-8"
            try:
                decoder = codecs.getincrementaldecoder(charset)("replace")
            except LookupError as error:
                raise PortalError(
                    "Portal declared an unsupported text encoding"
                ) from error

            stream = DirectoryStreamParser(
                final_url,
                excluded_keywords=(),
                strict=True,
            )
            for chunk in _iter_decompressed(response):
                stream.feed(decoder.decode(chunk))
            stream.feed(decoder.decode(b"", final=True))
            try:
                entries = stream.finalize()
            except ValueError as error:
                raise PortalError(
                    f"Portal returned an invalid directory listing: {error}"
                ) from error
            response_etag = response.getheader("ETag") or ""
            response_last_modified = response.getheader("Last-Modified") or ""
            reusable = True
        except (http.client.HTTPException, OSError) as error:
            raise PortalError(f"Unable to fetch {target_url}: {error}") from error
        finally:
            _finish_connection(self._pool, origin, conn, response, reusable)

        if cache is not None:
            try:
                cache.put(
                    target_url,
                    entries,
                    etag=response_etag,
                    last_modified=response_last_modified,
                )
            except CacheError as error:
                self.warning_callback(
                    f"Listing fetched but could not be cached: {error}"
                )
        return self._filter_entries(entries)


def _origin(url):
    return http_origin(url)
