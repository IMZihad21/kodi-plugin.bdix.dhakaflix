# Development and Testing

## Prerequisites

- Python 3.11 or later for parser and packaging utilities.
- Kodi installed on at least one target device for integration testing.
- Network access from the target Kodi device to the fixed DhakaFlix portal,
  source servers, and direct media URLs.
- Kodi Omega 21.3 or later; the add-on declares `xbmc.python` version `3.0.1`
  and uses Python 3.11-compatible syntax.

No separate HTTP server, database, Docker runtime, Go toolchain, or third-party
Python package is part of the planned add-on runtime.

## Suggested local workflow

1. Keep portal discovery, crawling, classification, the SQLite index, and `.strm`
   generation independent of Kodi imports.
2. Save representative portal responses as HTML fixtures, with private or
   user-specific data removed.
3. Run automated tests using the host Python interpreter:

   ```sh
   python3 -m unittest discover -s tests -v
   ```

4. Install the add-on directory into Kodi's `addons` directory for rapid
   development, then restart Kodi or reload add-ons.
5. Test first-run source registration and restart notification on the target
   Kodi profile. Verify existing sources are preserved and the `.dhakaflix.bak`
   backup is usable.
6. Verify that source registration in `sources.xml` survives a Kodi restart and
   that completed syncs trigger library scans.
7. Inspect Kodi's log after syncing, scanning, cleaning, browsing, and playing
   a sample item.
8. Build the installable ZIP:

   ```sh
   python3 tools/package.py
   ```

7. Test a clean ZIP installation on each declared
   target Kodi version.

## Private GitHub and CI/CD

Create an empty private GitHub repository, initialize and push this project as
described in the [repository README](../README.md), and enable GitHub Actions
for the repository. `.github/workflows/ci-cd.yml` runs configured Ruff linting,
tests on Python 3.11 and 3.15 on Linux, and Python 3.15 on Windows and macOS. It also
validates Kodi 21.3 metadata and Python 3.11 syntax, then uploads the
installable ZIP as a workflow artifact. Version tags publish that validated
artifact with its SHA-256 checksum.

To publish a private release, make sure the version in `addon.xml` is committed,
then push the corresponding `vMAJOR.MINOR.PATCH` tag. The workflow checks the
tag and publishes the ZIP and SHA-256 checksum to the private GitHub release.
Anyone installing from a private repository must have GitHub access to download
the asset; transfer the ZIP to Kodi over USB or a trusted local share.

Exact Kodi profile paths vary by operating system and installation. Use Kodi's
official add-on installation or development instructions for the target device
rather than assuming a single path.

## Test layers

### Unit tests

Test portal parsing and URL resolution against fixtures:

- Category menus with relative, absolute, and duplicate links.
- `h5ai` rows for directories, video files, images, subtitles, and parent
  directory entries.
- Escaped spaces, parentheses, brackets, percent encoding, and non-ASCII
  titles.
- Unsupported schemes, fragments, malformed rows, and empty pages.
- Keyword exclusions and common video extension classification.
- Homepage category discovery, excluded non-media categories, private-node
  restrictions, and duplicate links.
- Crawl failures, cancellation, deep trees, large directory pages, timestamp-
  based subtree skipping, full-audit scheduling, and incomplete-run safety.
- Movie/episode classification, alternate URLs, filename collisions, and
  generated `.strm` contents.
- Safe stale-file pruning, last-success semantics, and Kodi source XML
  preservation/idempotency/restart detection.

### Integration tests

Use a local fixture HTTP server when possible to exercise fetching, status
handling, timeout behavior, cache expiry, and refresh without relying on the
live portal.

### Kodi manual checks

- Add-on installs and opens without a traceback.
- First install registers both roots and notifies that Kodi must restart.
- Restart loads the registered roots and completed syncs trigger library scans.
- A fresh profile gets one automatic initial sync with at most three startup
  attempts; add-on updates do not auto-sync. Manual sync has no cooldown and
  never overlaps an active sync.
- Large crawls keep Kodi usable and report progress, completion, and failures.
- Incremental index migrations preserve prior media paths; full audits
  reconcile changes missed by h5ai's non-recursive, minute-resolution dates.
- Partial node failure never prunes files or advances last-success state.
- Successful sync discovers current homepage links, creates movie/episode
  `.strm` entries, and retains alternate URLs.
- Stale files are removed only from add-on-managed roots; Kodi's global clean
  is deliberately not requested, so stale Kodi database entries can remain.
- Kodi library scans produce usable Movies and TV Shows entries and playback
  reaches the original media nodes.
- Existing browsing, back navigation, refresh, artwork, and subtitles continue
  to work.

## Packaging

The final ZIP should contain one top-level add-on directory named to match the
add-on ID, including `addon.xml`, plugin and service entry points, Python
modules, resources, and the DhakaFlix icon.
Exclude test fixtures, development notes, bytecode caches, and local settings.

Before release, verify the `addon.xml` version, Kodi Python API requirement,
license metadata, and artwork. Install the ZIP into a clean Kodi profile to
catch missing files or undeclared dependencies. This workspace has not yet been
validated in a real Kodi runtime or against the live private portal.

## Troubleshooting

- **Sync did not start:** inspect last-success and failure state in Kodi's
  add-on profile and confirm the Kodi device can reach the portal homepage.
- **Sources need a restart:** restart Kodi after the add-on reports a
  `sources.xml` change; Kodi reads source configuration at startup.
- **Library remains empty:** confirm sync completed, restart Kodi if source
  paths were just registered, and check that the add-on's source paths appear
  in Kodi's video sources.
- **Sync failed or is incomplete:** read the detailed Kodi error dialog for
  the failing URL, filesystem path, or network error. Retry after correcting the
  reported issue.
- **Folders show no entries:** verify the fetched page is an actual directory
  listing and not an error page or redirect target.
- **Playback fails after selecting a file:** open the resolved media URL from
  the Kodi device's network, verify its HTTP response and byte-range support,
  and check Kodi's player log.
- **Works on one device only:** confirm both devices can route to the same
  source node; direct playback is performed by the Kodi client.
- **Stale listing:** use the add-on refresh action or reduce the configured
  cache TTL.
