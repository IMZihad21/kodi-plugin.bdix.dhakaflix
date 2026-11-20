# DhakaFlix Project Handoff

## Purpose

This repository contains the `plugin.bdix.dhakaflix` Kodi add-on (`v0.5.0`).
It is an on-demand browser and movie-search video plugin for DhakaFlix `h5ai`
listings, passing resolved media URLs directly to Kodi for playback. Media
bytes are not downloaded, proxied, or transcoded, and no background service or
`.strm` library synchronization is used.

The add-on targets Kodi Omega 21.3 and later through the `xbmc.python` 3.0.1
API using Python 3.11-compatible code and zero third-party Python dependencies.
Real-device testing on supported Kodi installations remains required before
marking a release production-ready.

## Agreed behavior

- **Ultra-minimal navigation scope:** Home menu exposes generic portal
  browsing (`http://172.16.50.7/`), curated movie categories, curated TV & series
  categories, movie search, cache clearing, and settings. Favourites/bookmarks
  and history are left to Kodi's built-in UI.
- **Configurable server nodes:** Per-node base URLs (`server_7_url`,
  `server_10_url`, `server_12_url`, `server_14_url`) control curated categories,
  movie search, and allowed origins; leaving a node blank disables it.
- **Movie-only search:** Search queries configured movie nodes (nodes 7 and 14)
  concurrently via the h5ai JSON search API, returns playable video files with
  an **Open parent folder** context menu item, caches results on disc across
  playback returns, and reports partial node failures via notifications.
- **Direct playback with cached extras:** Playback resolves the target URL
  after verifying its origin against approved servers, attaching matching
  adjacent subtitles, folder artwork, and metadata from the cached parent
  listing without extra HTTP requests.
- **Fast on-demand browsing:**
  - `<reuselanguageinvoker>true</reuselanguageinvoker>` with per-invocation
    context isolation and thread-safe client/cache reuse.
  - Persistent per-origin HTTP/1.1 keep-alive connection pool with `gzip` and
    `deflate` decompression and streaming HTML parsing.
  - Two-tier cache (64-entry process LRU in memory + `listing_cache.sqlite`
    bounded at 500 entries) with HTTP `ETag`/`Last-Modified` conditional `304 Not
    Modified` revalidation. Unfiltered entries are cached so changes to
    `excluded_keywords` or node settings apply immediately.
  - Batch `xbmcplugin.addDirectoryItems` with `offscreen=True` and Kodi 21
    `InfoTagVideo` metadata setters (`title`, `mediatype`, `year`, `season`,
    `episode`, `dateAdded`, `studios`) plus `label2` file sizes.
- **Upgrading from v0.4.0:** Previous `library/` files and `sources.xml`
  entries created by `v0.4.0` are left untouched on upgrade and documented for
  manual cleanup if desired.

## Code map

- `plugin.bdix.dhakaflix/default.py`: Kodi entry point, per-invocation context,
  menu/listing/search/playback rendering, and cache invalidation routes.
- `plugin.bdix.dhakaflix/resources/lib/portal.py`: Keep-alive `_ConnectionPool`,
  origin/redirect enforcement, gzip/deflate decoding, streaming directory
  fetches with `304` revalidation, and h5ai movie search.
- `plugin.bdix.dhakaflix/resources/lib/cache.py`: Two-tier in-memory LRU +
  SQLite listing cache with HTTP validator storage, touch, invalidation, clear,
  and legacy `.json` cleanup.
- `plugin.bdix.dhakaflix/resources/lib/parser.py`: Streaming `HTMLParser` for
  `h5ai` tables (`fb-i`/`fb-n`/`fb-d`/`fb-s`) and anchor fallback, O(1) file
  classification, strict error-page detection, size/date formatting, and
  conservative filename metadata parsing.
- `plugin.bdix.dhakaflix/resources/lib/catalog.py`: Fixed `PORTAL_URL`, node
  metadata (`7`, `10`, `12`, `14`), curated movie/series categories, and
  movie search nodes.
- `plugin.bdix.dhakaflix/resources/lib/router.py`: Allowlisted Kodi route
  builder and validator (`root`, `browse`, `play`, `refresh`, `settings`,
  `catalog`, `search`, `clear_cache`).
- `plugin.bdix.dhakaflix/resources/lib/urls.py`: LRU-cached host normalization,
  HTTP(S) URL validation, origin matching, and `parent_directory_url`.
- `plugin.bdix.dhakaflix/resources/lib/config.py`: Bounded integer setting
  parser.
- `tests/`: Standard-library unit and local HTTP integration tests across all
  modules and `default.py`.
- `tools/package.py`: Add-on metadata/settings/Python 3.11 AST validation and
  reproducible ZIP/checksum generation.

## Safety and operational details

- Only HTTP(S) URLs without embedded credentials are accepted; redirects and
  discovered/playable links are restricted to approved server origins (and same
  origin for search requests).
- Strict directory parsing rejects HTML challenge/error pages (ignoring
  `<script>`/`<style>` contents) and unrecognized non-listing responses while
  preserving empty valid directories.
- Search responses are bounded to `MAX_SEARCH_BYTES = 4 MiB`; listing cache is
  capped at 500 SQLite entries and 64 in-memory entries.
- SQLite connections in `ListingCache` open and close per operation so no
  persistent file locks block Windows/cross-platform profile cleanup.

## Local checks

Run from the repository root:

```sh
python -m unittest discover -s tests -v
python tools/package.py --output <temporary-directory>/dhakaflix-check.zip
```

CI runs configured Ruff linting and formatting checks (`pyproject.toml`), the
unit suite on Python 3.11 and 3.15 on Linux, and Python 3.15 on Windows and
macOS, plus metadata/AST packaging validation.

## Release validation still required

Host-side unit tests and local HTTP server tests mock Kodi's `xbmc*` APIs and
do not prove Kodi runtime behavior or live BDIX portal compatibility. Before
describing a release as production-ready, install the ZIP on supported Kodi
21.3+ devices and verify:

1. Navigation, curated movie and TV/series categories, back navigation, and
   `<reuselanguageinvoker>` stability across rapid clicks.
2. Movie search across reachable and unreachable nodes, **"Open parent folder"**,
   and playback return behavior.
3. Listing cache hits, HTTP `304` revalidation, **Refresh listing** (`RunPlugin`
   + `Container.Refresh`), and **Clear listing cache**.
4. Direct playback, adjacent subtitle loading, artwork display, and `InfoTagVideo`
   metadata rendering in Kodi's UI.
