# Kodi Add-on Design

## Add-on identity

Add-on ID: `plugin.bdix.dhakaflix`.

The add-on should be a standard Kodi Python plugin. Kodi launches `default.py`
with a plugin URL and handle; the entry point parses the requested action,
delegates portal operations to the library modules, and renders directory
entries through Kodi's plugin APIs.

The current `addon.xml` requires `xbmc.python` 3.0.0, targeting Kodi Matrix
(19) or later. Do not lower this requirement without testing the APIs and
settings schema on the older Kodi release.

## Request and browse flow

1. Kodi invokes the add-on root route.
2. The add-on shows generic portal browsing, curated movie/series categories,
   search, and settings.
3. Category shortcuts are built from configured node URLs; generic browsing
   discovers links dynamically from the selected page.
4. Search queries relevant configured nodes concurrently, validates and
   deduplicates supported video links, and reports partial source failures.
5. Selecting a folder fetches and parses that page on demand.
6. Selecting a video creates a playable `ListItem` whose path is the resolved
   origin URL, attaching adjacent matching subtitles when available.
7. Kodi's player fetches the media directly from the source.

The add-on should not fetch video content just to build a listing. HEAD requests
or metadata probing may be added later only where necessary and should have
short timeouts.

## Module responsibilities

- `default.py`: Kodi entry point, argument parsing, dispatch, and top-level
  error presentation.
- `router.py`: map safe plugin actions and URL parameters to operations.
- `portal.py`: HTTP requests, response status/encoding handling, directory
  fetching, and the h5ai JSON search API.
- `catalog.py`: curated category paths and node-specific search support.
- `parser.py`: convert portal HTML into typed category, directory, image, and
  video entries.
- `urls.py`: normalize and validate HTTP(S) URLs.
- `cache.py`: TTL cache keyed by normalized absolute page URL; refresh bypasses
  or deletes the selected key.
- Kodi adapter code: construct folder/playable list items, set artwork and
  content type, and call Kodi's directory or playback APIs.

Keep the parser independent of Kodi imports so fixture-based tests can run
outside Kodi.

## HTML and URL handling

- Prefer Python's standard-library `html.parser` over regular expressions for
  extracting nested/quoted HTML attributes and text.
- Support the observed `h5ai` table structure and a conservative generic
  anchor fallback.
- Decode HTML entities, trim display text, and resolve relative links against
  the page URL.
- Ignore parent-directory links, fragments, `javascript:` links, and
  unsupported schemes.
- Restrict fetched and playable URLs to HTTP or HTTPS. Do not follow links to
  local files or unrelated schemes.
- Determine directory status from `h5ai` icon/link metadata when available;
  otherwise use a trailing slash as a fallback, not as the only signal.
- Classify files using a tested allowlist of common video extensions. Keep
  images and subtitle files as separate types.
- Sort folders and files consistently while retaining source names for display
  and playback diagnostics.
- Apply exclusions to category labels and URLs, with defaults that omit games
  and software while allowing users to change the list.

## Playback and metadata

Start with direct URL playback. Set a clear title and video media type on the
Kodi `ListItem`; use the fully resolved absolute URL as the item path. Do not
rewrite source URLs or redirect through a custom service.

For folder-based movies and shows, preserve navigation first. Derive a clean
title, year, season, or episode number only when the filename/folder pattern is
unambiguous. Avoid fabricating metadata when parsing fails.

Artwork should be optional: select likely poster files by filename preference
and provide them as art only when a valid image URL is found. Kodi should still
be able to browse and play if artwork is absent.

External subtitle behavior depends on the selected Kodi player and source
format. Validate it on real target devices before adding custom subtitle
download logic.

## Settings

Expose user settings through Kodi's settings API:

- Portal start URL (default may point to the documented DhakaFlix portal).
- Base URLs for the curated DhakaFlix nodes; a blank node URL disables its
  category shortcuts and search.
- Excluded category keywords.
- Listing cache duration.
- Request timeout for slow source nodes.

The generic directory browser and curated multi-node categories/search are both
available from the add-on home menu. The node/category mapping is based on
observed DhakaFlix layouts and must be checked against the live BDIX service.

Do not store credentials unless the origin adds authentication and a specific
secure credential mechanism is designed.

## Cache and refresh

Cache parsed directory results by normalized page URL and expire them after the
configured TTL. Cache only listings and lightweight metadata, not media bytes.

A refresh action should invalidate the current page and reload it. Avoid a
background crawler or full-site scan; Kodi navigation naturally drives
on-demand requests.

## Failure handling and diagnostics

Handle network timeouts, DNS errors, non-success HTTP responses, unexpected
content, and malformed entries explicitly. Present a short user-facing Kodi
message and log the URL/status and failure detail to Kodi's log without
including secrets.

An empty but valid directory is different from a failed request. Preserve that
distinction in the parser and UI behavior.

## Privacy and network assumptions

The listed portal addresses are private-network addresses. A Kodi device must
have a route to the ISP/LAN portal; internet connectivity alone may not be
enough. Direct playback also requires the player device to reach the media
node, not merely the device where a separate service might run.

The add-on makes requests only when the user browses a listing and only
accepts HTTP(S) URLs without embedded credentials. Redirect targets are also
validated. Cross-host links are permitted because the portal uses multiple
private-network nodes; actual Kodi-device reachability is a deployment
requirement.
