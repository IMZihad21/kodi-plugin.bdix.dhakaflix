# DhakaFlix Project Handoff

## Purpose

This repository contains the `plugin.bdix.dhakaflix` Kodi add-on. It retains
the interactive portal browser and synchronizes DhakaFlix movie and TV
listings into Kodi's local video library using `.strm` files. A stream file
contains the original media URL; media bytes are not downloaded, proxied, or
transcoded.

The add-on targets Kodi Omega 21.3 and later through the `xbmc.python`
3.0.1 API, using Python 3.11-compatible code. The Kodi service and
source-registration behavior still need validation on actual supported Kodi
devices before release.

## Agreed behavior

- Discover movie and TV/series categories from the fixed portal homepage
  `http://172.16.50.7/`; the portal structure and node links can change.
- Include movies and TV/series; exclude games, software, tutorials,
  documentaries, wrestling, and awards shows from library sync.
- Route every item from a TV/series category under the TV Shows root, grouping
  by inferred series and season when available. Keep movie-category items under
  Movies.
- Keep each distinct media URL, including alternate encodes.
- Store generated library data in the add-on profile under `library/Movies`,
  `library/TV Shows`, and a SQLite media/directory index; keep sync lifecycle
  state in `state.json`.
- Run a full catalog crawl initially and when a sync is triggered at least
  seven days after the previous full crawl. The interval chooses full versus
  incremental behavior; it does not schedule a sync. Between full crawls,
  compare h5ai directory dates with the SQLite index and skip subtrees whose
  displayed date is unchanged.
- Provide separate manual incremental-sync and full-rescan actions. Incremental
  sync checks changed folders; full rescan revisits every selected category and
  folder regardless of the seven-day interval.
- Reclassification after output or category-policy changes occurs during the
  next full crawl; after initial startup retries, the user must trigger syncs.
- Automatically sync once for a fresh add-on profile. Retry an unsuccessful
  or incomplete initial sync on subsequent Kodi startups, up to three total
  attempts. Do not auto-sync on add-on updates. After success or retry
  exhaustion, only manual sync triggers further synchronization.
- Show a persistent background progress dialog while crawling. Do not start a
  second sync while one is marked running. Manual sync has no cooldown.
  Requests queued while a run is active wait until it finishes. A successful
  manual sync satisfies the initial sync.
- Show detailed Kodi error dialogs for failed syncs, service errors, and
  browser errors; no add-on-specific file logger is used.
- Do not prune managed `.strm` files after an incomplete or empty crawl. Prune
  only after a complete crawl. Large proposed prunes require confirmation by
  the same complete result on a later sync. Kodi's global video-library clean
  is not run because it can affect unrelated missing items in the profile.
- Register Movies and TV Shows roots in Kodi's `sources.xml`, preserve other
  sources, back up before editing, and notify when a Kodi restart is needed.
- Create and verify both library root directories before registering them as
  writable Kodi sources so Kodi never receives a missing or invalid path.
- Library scans resume automatically after registered paths are loaded at
  Kodi startup. Scraper selection remains an optional Kodi-side choice.

## Code map

- `plugin.bdix.dhakaflix/default.py`: Kodi plugin UI, browsing/search, playback,
  and manual-sync request entry point.
- `plugin.bdix.dhakaflix/service.py`: startup scheduling, manual request
  processing, source registration, sync lifecycle, scan/clean sequencing, and
  user notifications.
- `plugin.bdix.dhakaflix/resources/lib/sync.py`: Kodi-independent category
  discovery, full/incremental crawls, media classification, SQLite
  index updates, `.strm` creation, and safe pruning.
- `plugin.bdix.dhakaflix/resources/lib/library_index.py`: SQLite inventory,
  legacy manifest migration, and transactional snapshot updates.
- `plugin.bdix.dhakaflix/resources/lib/portal.py`: HTTP and portal listing
  access, URL validation, and search.
- `plugin.bdix.dhakaflix/resources/lib/library.py`: managed paths, manual
  request marker, and backed-up atomic source registration.
- `plugin.bdix.dhakaflix/resources/lib/parser.py`: directory-listing parser.
- `plugin.bdix.dhakaflix/resources/lib/router.py`: validated add-on routes.
- `plugin.bdix.dhakaflix/resources/lib/catalog.py`: curated browser categories
  and configured search nodes.
- `tests/`: standard-library unit and integration-style tests.
- `tools/package.py`: add-on metadata/Python compatibility validation and
  reproducible ZIP/checksum generation.
- `docs/KODI-ADDON.md` and `docs/DEVELOPMENT.md`: detailed design, development,
  installation, and device-test documentation.

The synchronization core is independent of Kodi imports and can be tested
with the host Python interpreter.

## Safety and operational details

- Crawls have no page, depth, media, category, duration, or directory-response
  size caps. Concurrency, URL validation, and shutdown cancellation remain
  bounded.
- The h5ai `Last modified` value is minute-resolution and reflects the
  directory entry itself, not the newest change anywhere below it. Incremental
  sync is therefore best-effort: a change below a skipped unchanged directory
  can be missed until a later full crawl.
- Any failed category/directory fetch makes the run incomplete.
  Incomplete runs preserve the previous SQLite inventory and do not authorize
  pruning or advance the last-success timestamp.
- Generated paths are sanitized and constrained to the add-on-managed library.
- Generated absolute paths are shortened with stable hash suffixes when needed
  to stay within a 240-unit/byte limit, and individual components are limited
  to 180 UTF-8 bytes for Windows, macOS, and Linux filesystem compatibility.
- Source registration edits are not Kodi's recommended GUI workflow. The
  original XML is backed up as `sources.xml.dhakaflix.bak`; malformed or
  conflicting configuration must be reported rather than overwritten.
- Library scan/clean built-ins and Kodi monitor event sequencing are
  asynchronous and have not yet been verified on a real Kodi installation.
- Direct playback depends on the Kodi device being able to reach each media
  node.

## Local checks

Run from the repository root:

```sh
python -m unittest discover -s tests -v
python tools/package.py --output <temporary-directory>/dhakaflix-check.zip
```

Detailed operation failures are presented in Kodi dialogs. There is no
add-on-specific log file.

CI runs configured Ruff linting and formatting checks, the unit suite on Python
3.11 and 3.15 on Linux, and Python 3.15 on Windows and macOS. The package
validator checks add-on metadata and Python 3.11 syntax compatibility.
Version-tagged releases publish the validated ZIP artifact with its SHA-256
checksum.

## Release validation still required

Host-side unit tests and package validation do not prove Kodi runtime
compatibility. Before describing a release as production-ready, install the ZIP
on supported Kodi versions and verify:

1. First-run source registration, backup, restart notice, and source loading
   after restart.
2. Fresh-install automatic sync, startup retry counts/exhaustion, update
   behavior, manual sync, cancellation, progress, and recovery after network
   or filesystem errors.
3. Complete and partial crawl behavior, alternate URLs, generated naming,
   pruning boundaries, and stale Kodi database behavior.
4. Kodi library scan completion, optional scraper results, direct playback, and
   preservation of unrelated library sources/items.

Latest host validation must be refreshed before release. A bounded live
homepage category check succeeded earlier. The full catalog crawl and
Kodi-device checks remain release validation items.
