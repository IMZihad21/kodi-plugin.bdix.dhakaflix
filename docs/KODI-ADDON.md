# Kodi Add-on Design

## Add-on identity

Add-on ID: `plugin.bdix.dhakaflix`.

The add-on provides a standard Kodi Python plugin and a startup service. Kodi
launches `default.py` for browsing and manual controls; `service.py` checks the
last successful sync at startup and performs due or manually requested
library synchronization in the background.

The add-on targets Kodi Omega 21.3 or later, requiring `xbmc.python` 3.0.1 and
Python 3.11-compatible code. Keep the API requirement and syntax validator in
sync with the minimum supported Kodi runtime.

## Request and browse flow

1. Kodi invokes the add-on root route.
2. The add-on shows generic portal browsing, curated movie/series categories,
   search, and settings.
3. Category shortcuts use configured DHAKA-FLIX node URLs; generic browsing
   discovers links dynamically from the fixed portal root.
4. Search queries configured relevant nodes concurrently, validates and
   deduplicates supported video links, and reports partial source failures.
5. Selecting a folder fetches and parses that page on demand.
6. Selecting a video creates a playable `ListItem` whose path is the resolved
   origin URL, attaching adjacent matching subtitles when available.
7. Kodi's player fetches the media directly from the source.

## Library synchronization flow

1. On a fresh add-on profile, the service starts one automatic full sync. If
   the attempt fails or is incomplete, it retries on later Kodi startups, up
   to three total attempts. Existing profiles are not automatically synced on
   add-on update. Once the initial sync succeeds or retries are exhausted,
   future syncs are manual only. The add-on home menu can queue a manual sync
   at any time except while another sync is running; manual sync has no
   cooldown. The home menu offers **Incremental library sync** to check changed
   folders and **Full library rescan** to revisit the selected catalog. A
   successful manual sync satisfies the initial sync. A background progress
   dialog remains visible during the crawl.
2. The sync fetches the fixed portal homepage, discovers current movie
   and TV/series category links, and recursively traverses those h5ai listings
   with bounded concurrency and no page, depth, media, category, duration, or
   directory-response-size caps. Games, software, tutorials, documentaries,
   wrestling, and awards shows are excluded from library sync. The initial sync
   visits the complete catalog. Later syncs run a full audit when at least
   seven days have elapsed since the previous full crawl; the interval does not
   schedule a sync. **Incremental library sync** uses h5ai folder dates to skip
   unchanged subtrees; **Full library rescan** always revisits all selected
   categories and folders.
3. Every video discovered under a TV/series category is placed under the TV
   Shows root, grouped by inferred series and season folders. Other supported
   videos are placed under Movies. Every distinct URL, including alternate
   encodes, is retained.
4. `.strm` files point to the source URLs; media bytes are not fetched for
   import. Movies and TV Shows are written to separate roots in the add-on's
   Kodi profile. Generated absolute paths are shortened with stable hash
   suffixes when needed, and components are byte-limited for Windows, macOS,
   and Linux filesystem compatibility.
5. A complete crawl is required before stale index-owned files are pruned.
   Category/directory failures leave the previous library generation intact.
   A full audit reclassifies entries and successful pruning also removes empty
   directories under the managed Movies and TV Shows roots. Kodi's global
   library clean is not run, to avoid removing unrelated missing items; Kodi
   may retain stale database entries for deleted `.strm` files.
6. The service creates and verifies both library root directories, then
   registers those roots in Kodi's `sources.xml`, preserving other sources and
   keeping a backup. A changed file triggers a restart-required notification;
   scans resume on the next Kodi startup.
7. After every complete sync, the service automatically scans Movies and TV
   Shows in sequence. A successful prune does not trigger Kodi's global
   video-library clean.

The add-on settings are grouped into **Sync** (movie and series selection) and
**Discovery** (node URLs, listing exclusions, cache duration, and request
timeout). Node URL settings affect browsing and search only; sync uses its own
fixed portal sources. Metadata scraper selection remains under Kodi's control.

The add-on does not download media content for browsing or synchronization.

## Module responsibilities

- `default.py`: Kodi entry point, argument parsing, dispatch, and top-level
  error presentation, including manual-sync requests.
- `service.py`: startup scheduling, manual request processing, source
  registration, Kodi scan/clean orchestration, and user notifications.
- `router.py`: map safe plugin actions and URL parameters to operations.
- `portal.py`: HTTP requests, response status/encoding handling, directory
  fetching, and the h5ai JSON search API.
- `catalog.py`: curated category paths and node-specific search support.
- `sync.py`: Kodi-independent portal category discovery, bounded full and
  incremental crawls, conservative media classification, and `.strm`
  generation.
- `library_index.py`: SQLite media/directory inventory and legacy manifest
  migration.
- `library.py`: local library paths, manual request queue, and atomic
  `sources.xml` registration.
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

## Add-on settings

Expose user settings through Kodi's settings API:

- **Sync:** movie sync, enabled by default, and TV/series sync, disabled by
  default.
- **Discovery:** excluded category keywords, listing cache duration, and
  request timeout for slow source nodes.

Sync media-type settings filter discovered categories before crawling. When a
previously enabled type is disabled, its managed entries are pruned on a
complete sync if at least one selected media item remains. Empty crawls
continue to preserve existing library data.

The generic directory browser and curated multi-node categories/search are both
available from the add-on home menu. The portal root is fixed.
Sync category links are discovered from the portal homepage; curated
browsing/search paths remain based on observed node layouts. Both behaviors
must be checked against the live BDIX service.

Do not store credentials unless the origin adds authentication and a specific
secure credential mechanism is designed.

## Cache and refresh

Cache parsed directory results by normalized page URL and expire them after the
configured TTL. Cache only listings and lightweight metadata, not media bytes.

A refresh action should invalidate the current page and reload it. The library
sync is a distinct, user-triggered/startup background crawl with bounded
concurrency and complete-versus-partial state, without crawl count, duration,
or directory-response-size caps. It performs a full crawl
when a sync runs after the seven-day interval and uses timestamp-based subtree
skipping between those audits. After the initial sync/retries, the user must
manually trigger syncs; the interval does not schedule them.

h5ai displays directory modification times to the minute. These timestamps
describe each directory's own entries; they do not propagate changes made
deeper in the tree. Incremental updates are best-effort, and a change inside a
skipped subtree may not be detected until a later full audit.

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

The add-on requests pages when the user browses or when an initial/manual
library sync runs. It only accepts HTTP(S) URLs without embedded credentials;
redirect targets are validated. Sync category and child-directory links are
restricted to private-network addresses or the portal host. Actual
Kodi-device reachability is a deployment requirement.

Kodi's documentation recommends managing media sources through the GUI rather
than editing `sources.xml`. Automatic registration is intentionally attempted
for this project, but is unsupported and must be validated on each target
platform/version. Kodi scraper selection remains optional and under Kodi's
control for metadata. The add-on registers paths and requests scans without a
separate setup confirmation. A global video-library clean can remove other
missing library items and is deliberately not requested after this add-on
prunes stale entries.
