"""Safe Kodi plugin URL construction and parsing."""

from dataclasses import dataclass
from urllib.parse import parse_qs, quote, urlencode

from resources.lib.urls import URLValidationError, normalize_http_url


class RouteError(ValueError):
    """Raised when a Kodi plugin route is malformed or unsafe."""


@dataclass(frozen=True)
class Route:
    action: str
    target_url: str = ""
    media_type: str = ""


def plugin_url(base_url, action, target_url=None, media_type=None):
    if action not in (
        "root",
        "browse",
        "play",
        "refresh",
        "settings",
        "catalog",
        "search",
        "sync",
    ):
        raise RouteError(f"Unsupported action: {action}")
    params = {"action": action}
    if target_url:
        params["url"] = normalize_http_url(target_url)
    if media_type:
        params["type"] = media_type
    return "{}?{}".format(base_url, urlencode(params, quote_via=quote, safe=""))


def parse_route(base_url, query_string):
    if not base_url.startswith("plugin://"):
        raise RouteError("Invalid Kodi plugin base URL")
    params = parse_qs(query_string.lstrip("?"), keep_blank_values=True)
    action = params.get("action", ["root"])[0]
    if action not in (
        "root",
        "browse",
        "play",
        "refresh",
        "settings",
        "catalog",
        "search",
        "sync",
    ):
        raise RouteError(f"Unsupported action: {action}")

    target = params.get("url", [""])[0]
    media_type = params.get("type", [""])[0]
    if action in ("browse", "play", "refresh"):
        if not target:
            raise RouteError(f"The {action} route requires a URL")
        try:
            target = normalize_http_url(target)
        except URLValidationError as error:
            raise RouteError(str(error)) from error
    elif target:
        raise RouteError("This route does not accept a URL")
    if action == "catalog" and media_type not in ("movie", "series"):
        raise RouteError("The catalog route requires a supported type")
    if action == "search" and media_type not in ("movie", "series"):
        raise RouteError("Search requires a supported type")
    elif action not in ("catalog", "search") and media_type:
        raise RouteError("This route does not accept a type")
    if "query" in params:
        raise RouteError("Search text is not accepted in plugin routes")
    return Route(action, target, media_type)
