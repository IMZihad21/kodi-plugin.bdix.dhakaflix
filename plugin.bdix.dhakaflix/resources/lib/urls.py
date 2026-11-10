"""HTTP URL validation and normalization."""

import ipaddress
from urllib.parse import quote, urlsplit, urlunsplit


class URLValidationError(ValueError):
    """Raised for invalid or unsupported source URLs."""


def http_origin(url):
    parts = urlsplit(url)
    scheme = parts.scheme.casefold()
    port = parts.port or (80 if scheme == "http" else 443 if scheme == "https" else None)
    return scheme, (parts.hostname or "").casefold(), port


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
    if any(char.isspace() or char in "\\/%" for char in host):
        raise URLValidationError("URL host contains invalid characters")

    try:
        address = ipaddress.ip_address(host)
        normalized_host = str(address)
        if address.version == 6:
            normalized_host = f"[{normalized_host}]"
    except ValueError:
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
    netloc = normalized_host
    if port is not None:
        netloc = f"{netloc}:{port}"
    path = quote(parts.path or "/", safe="/%:@!$&'()*+,;=-._~")
    query = quote(parts.query, safe="=&?/:;+,%@!$'()*-._~")
    return urlunsplit((scheme, netloc, path, query, ""))
