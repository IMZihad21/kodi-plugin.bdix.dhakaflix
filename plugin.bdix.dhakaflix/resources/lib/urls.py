"""HTTP URL validation and normalization."""

import ipaddress
from functools import lru_cache
from urllib.parse import quote, urlsplit, urlunsplit


class URLValidationError(ValueError):
    """Raised for invalid or unsupported source URLs."""


def http_origin(url):
    parts = urlsplit(url)
    scheme = parts.scheme.casefold()
    port = parts.port or (
        80 if scheme == "http" else 443 if scheme == "https" else None
    )
    return scheme, (parts.hostname or "").casefold(), port


@lru_cache(maxsize=256)
def _normalize_host(host):
    if any(char.isspace() or char in "\\/%" for char in host):
        raise URLValidationError("URL host contains invalid characters")

    try:
        address = ipaddress.ip_address(host)
        normalized_host = str(address)
        if address.version == 6:
            normalized_host = f"[{normalized_host}]"
        return normalized_host
    except ValueError:
        pass

    try:
        normalized_host = host.encode("idna").decode("ascii").casefold()
    except UnicodeError as error:
        raise URLValidationError("URL host is malformed") from error
    labels = normalized_host.rstrip(".").split(".")
    if len(normalized_host) > 253 or any(
        not label
        or len(label) > 63
        or label.startswith("-")
        or label.endswith("-")
        or not all(char.isalnum() or char == "-" for char in label)
        for label in labels
    ):
        raise URLValidationError("URL host is malformed")
    return normalized_host


def normalize_http_url(url):
    if not isinstance(url, str):
        raise URLValidationError("URL must be text")
    value = url.strip()
    if not value or any(ord(char) < 32 for char in value):
        raise URLValidationError("URL is empty or contains control characters")
    try:
        parts = urlsplit(value)
        scheme = parts.scheme.casefold()
        host = parts.hostname
        port = parts.port
    except ValueError as error:
        raise URLValidationError(f"URL is malformed: {error}") from error
    if scheme not in ("http", "https") or not host:
        raise URLValidationError("Only absolute HTTP and HTTPS URLs are allowed")
    if parts.username is not None or parts.password is not None:
        raise URLValidationError("URLs containing credentials are not allowed")

    netloc = _normalize_host(host)
    if port is not None:
        netloc = f"{netloc}:{port}"
    path = quote(parts.path or "/", safe="/%:@!$&'()*+,;=-._~")
    query = quote(parts.query, safe="=&?/:;+,%@!$'()*-._~")
    return urlunsplit((scheme, netloc, path, query, ""))


def parent_directory_url(url):
    """Return the normalized parent directory URL for a file or folder URL."""
    normalized = normalize_http_url(url)
    parts = urlsplit(normalized)
    path = parts.path or "/"
    if path == "/":
        parent_path = "/"
    else:
        trimmed = path.rstrip("/")
        slash_index = trimmed.rfind("/")
        parent_path = trimmed[: slash_index + 1] if slash_index >= 0 else "/"
    return urlunsplit((parts.scheme, parts.netloc, parent_path, "", ""))
