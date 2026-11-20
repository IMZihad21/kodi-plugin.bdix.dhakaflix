# Kodi Add-on Design

## Add-on identity

Add-on ID: `plugin.bdix.dhakaflix` (`v0.5.0`).

The add-on is a pure on-demand Kodi Python video plugin (`default.py`) with
`<reuselanguageinvoker>true</reuselanguageinvoker>` enabled for low-latency
folder navigation and zero background service overhead.

It targets Kodi Omega 21.3 or later, requiring `xbmc.python` 3.0.1 and Python
3.11-compatible standard-library code.

## Request, browse, and playback flow

1. Kodi invokes `default.py` with fresh `sys.argv` (`base_url`, `handle`,
   `query_string`). All request context is scoped per invocation; only
   thread-safe `PortalClient` (keep-alive pool) and `ListingCache` (memory LRU +
   SQLite) instances are reused across invoker calls and recreated whenever
   settings change.
2. The home menu exposes:
   - **Browse DhakaFlix portal** (`http://172.16.50.7/`)
   - **Movie categories** (nodes 7 and 14)
   - **TV & series categories** (nodes 10, 12, and 14)
   - **Search movies** (nodes 7 and 14)
   - **Clear listing cache**
   - **Add-on settings**
3. Selecting a folder fetches or revalidates that listing on demand, batches
   `offscreen=True` `ListItem` tuples via `xbmcplugin.addDirectoryItems`,
   populates Kodi 21 `InfoTagVideo` fields and `label2` file sizes, and sets
   unsorted/label/date sort methods.
4. Selecting **Search movies** opens `xbmc.Keyboard`, queries configured movie
   nodes concurrently over the h5ai JSON search API, deduplicates video URLs,
   attaches **Open parent folder** context menus, caches the container to disc
   across playback returns, and warns via notification on partial node failures.
5. Selecting a video invokes the `play` route: after validating the target
   origin, `_play` resolves the original URL via `xbmcplugin.setResolvedUrl`,
   attaching matching adjacent subtitles, folder artwork, and metadata from the
   cached parent listing without making extra HTTP requests.

## Module responsibilities

- `default.py`: Kodi entry point, per-invocation `_Context`, `reuselanguageinvoker`
  client/cache management, UI rendering, and error presentation.
- `router.py`: allowlisted Kodi plugin route construction and parsing (`root`,
  `browse`, `play`, `refresh`, `settings`, `catalog`, `search`, `clear_cache`).
- `portal.py`: thread-safe HTTP/HTTPS keep-alive `_ConnectionPool`, redirect and
  origin validation, `gzip`/`deflate` decompression, streaming directory fetch
  with HTTP `304 Not Modified` revalidation, and concurrent-ready movie search.
- `cache.py`: two-tier cache (64-entry process LRU in memory + 500-entry
  `listing_cache.sqlite`) storing parsed entries alongside `ETag` and
  `Last-Modified` validators, supporting `touch`, `invalidate`, `clear`, and
  legacy `.json` cache cleanup.
- `parser.py`: streaming `HTMLParser` (`DirectoryStreamParser`) supporting
  `h5ai` table rows (`fb-i`, `fb-n`, `fb-d`, `fb-s`) and generic `<a>`
  fallback, O(1) extension classification, strict error/challenge detection,
  file size formatting, Kodi date formatting, and conservative filename metadata
  parsing.
- `catalog.py`: fixed `PORTAL_URL`, node definitions (`7`, `10`, `12`, `14`),
  curated movie/series shortcuts, and `search_nodes("movie", ...)`.
- `urls.py`: LRU-cached host normalization, HTTP(S) URL validation,
  `http_origin`, and `parent_directory_url`.
- `config.py`: validated integer setting parsing.

Keep all `resources/lib/` modules free of Kodi (`xbmc*`) imports so unit and
local HTTP tests run directly on the host Python interpreter.

## HTML and URL handling

- Parse HTML with Python's standard-library `html.parser` using streaming
  incremental UTF-8/charset decoding directly into `DirectoryStreamParser`.
- Skip `<script>` and `<style>` blocks when scanning strict-mode error markers
  to avoid false positives from page scripts/styles.
- Support `h5ai` table rows (`fb-i`, `fb-n`, `fb-d`, `fb-s`) and fall back to
  generic `<a>` tags only when no `h5ai` rows are present.
- Decode HTML entities, normalize whitespace, resolve relative links, and drop
  parent links, fragments, `javascript:`, and non-HTTP(S) schemes.
- Restrict fetched, redirected, and playable URLs to approved HTTP(S) server
  origins without embedded credentials.
- Classify files via O(1) extension lookup across allowlisted video, image, and
  subtitle extensions, and sort deterministically folders-first.
- Apply `excluded_keywords` and `allowed_origins` filtering dynamically after
  cache retrieval so settings edits apply immediately.

## Playback and metadata

- Pass the resolved origin URL directly to Kodi via `setResolvedUrl`; never
  proxy or transcode media bytes.
- Populate Kodi 21 `InfoTagVideo` setters (`setTitle`, `setMediaType`,
  `setYear`, `setSeason`, `setEpisode`, `setDateAdded`, `setStudios`) only from
  unambiguous filename/timestamp tokens; fall back to `setInfo` only when
  `getVideoInfoTag` is unavailable.
- Display formatted file sizes in `ListItem.label2`.
- Attach artwork and matching same-stem subtitles from both folder listings
  and cached parent listings during `play` resolution.

## Add-on settings

- **Discovery:** `excluded_keywords`, `cache_ttl` (seconds; `0` disables cache),
  and `request_timeout` (3–60 seconds).
- **DhakaFlix servers:** `server_7_url`, `server_10_url`, `server_12_url`, and
  `server_14_url`. Clearing any node URL disables its curated categories and
  movie search.

## Cache and refresh

- Cache unfiltered directory entries keyed by normalized absolute URL in memory
  (LRU 64 entries) and `listing_cache.sqlite` (LRU 500 entries) along with
  HTTP `ETag` and `Last-Modified` headers.
- Expired cached entries remain eligible for conditional `If-None-Match` /
  `If-Modified-Since` requests; on `304 Not Modified`, `ListingCache.touch()`
  refreshes the TTL without re-downloading or re-parsing HTML.
- Context-menu **Refresh listing** runs via Kodi `RunPlugin` (`handle = -1`),
  invalidates the target URL in cache, and calls
  `xbmc.executebuiltin("Container.Refresh")`. Direct `refresh` route calls
  re-fetch and render the directory.
- **Clear listing cache** clears both memory and SQLite entries and removes
  legacy `<sha256>.json` cache files from the profile directory.

## Failure handling and diagnostics

- Report invalid settings, malformed routes, unapproved origins, HTTP errors,
  corrupt caches, and unexpected content explicitly via Kodi error dialogs and
  `xbmc.log` (`[plugin.bdix.dhakaflix]`).
- Keep empty valid directories distinct from failed HTTP requests or error pages.
- Degrade partial movie-search node failures to a non-blocking notification when
  other nodes return results.

## Privacy and network assumptions

- Portal and node defaults use private-network IP addresses (`172.16.50.*`)
  requiring BDIX/LAN reachability directly from the Kodi playback device.
- No credentials are stored or prompted unless source servers change.
