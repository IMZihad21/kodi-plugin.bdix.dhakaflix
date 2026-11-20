# Development and Testing

## Prerequisites

- Python 3.11 or later for running tests, linting, and packaging.
- Kodi Omega 21.3 or later on at least one target device for manual testing.
- Direct BDIX/LAN reachability from the Kodi device to the DhakaFlix portal
  and media nodes.
- The add-on declares `xbmc.python` version `3.0.1` and maintains Python 3.11
  syntax compatibility with zero third-party runtime dependencies.

## Suggested local workflow

1. Keep HTTP access, HTML parsing, routing, caching, catalog, and URL logic
   inside `plugin.bdix.dhakaflix/resources/lib/` free of Kodi imports.
2. Run Ruff checks and the unit/integration test suite using the host Python
   interpreter:

   ```sh
   ruff check .
   ruff format --check .
   python3 -m unittest discover -s tests -v
   ```

3. Build and validate the installable ZIP (checks `addon.xml`,
   `reuselanguageinvoker`, `settings.xml`, assets, and Python 3.11 AST syntax):

   ```sh
   python3 tools/package.py
   ```

4. For rapid local iteration, copy `plugin.bdix.dhakaflix/` into Kodi's
   `addons/` directory and restart Kodi, or install the built ZIP from
   `dist/plugin.bdix.dhakaflix-0.5.0.zip`.
5. Test browsing, movie search, playback, subtitles, refresh, cache clearing,
   and `kodi.log` on real target Kodi installations.

## Private GitHub and CI/CD

`.github/workflows/ci-cd.yml` runs configured Ruff checks (`pyproject.toml`),
executes `unittest` on Python 3.11 and 3.15 on Linux plus Python 3.15 on Windows
and macOS, runs `tools/package.py` validation on Python 3.11, and uploads the
ZIP + SHA-256 artifact. Pushing a `vMAJOR.MINOR.PATCH` tag matching `addon.xml`
publishes the release assets to the repository.

## Test layers

### Unit and local HTTP tests (`tests/`)

- `test_parser.py`: `h5ai` row parsing (`fb-i`/`fb-n`/`fb-d`/`fb-s`), generic
  anchor fallback, streaming chunk parsing, file size/date formatting,
  conservative metadata parsing, strict error detection (including script/style
  skipping), keyword exclusions, and URL normalization/`parent_directory_url`.
- `test_cache.py`: two-tier memory + SQLite round-trip, TTL expiry, stale
  validator storage and `touch()`, `invalidate()`, `clear()` (including legacy
  `.json` cleanup), 500-entry eviction, `ttl=0`, and corrupt cache errors.
- `test_portal.py`: local HTTP/1.1 fixture server verifying keep-alive socket
  reuse, `gzip`/`deflate` decoding, `304 Not Modified` revalidation, >8 MiB
  listings, dynamic keyword filtering, movie search size/time parsing, HTTP
  error handling, and cross-origin/unapproved redirect refusal.
- `test_catalog.py` & `test_router.py`: curated categories, movie-only search
  nodes, route encoding/validation, and `clear_cache` routes.
- `test_config.py`: bounded integer setting validation.
- `test_entrypoint.py`: mocked Kodi (`xbmc*`) tests verifying home menu,
  batched `addDirectoryItems` + `offscreen=True` + `InfoTagVideo`, movie search
  + parent folder context menu, cached subtitle/artwork attachment during
  playback, `RunPlugin` refresh, `clear_cache`, and `reuselanguageinvoker`
  isolation across repeated `main()` calls.
- `test_packaging.py`: metadata, settings, `reuselanguageinvoker`, deterministic
  ZIP layout, and SHA-256 verification.

### Kodi manual checks

- Clean install and upgrade over `v0.4.0` open without tracebacks.
- Portal browsing, curated Movie and TV/series categories, and back navigation
  stay fast across repeated clicks (`reuselanguageinvoker`).
- Movie search returns playable results across reachable nodes, opens parent
  folders cleanly, and handles unreachable nodes without modal error storms.
- Direct playback starts smoothly with matching subtitles, artwork, and
  `InfoTagVideo` details.
- Context-menu **Refresh listing** and home **Clear listing cache** refresh
  listings as expected.

## Packaging

The built ZIP contains one top-level `plugin.bdix.dhakaflix/` directory with
`addon.xml`, `default.py`, `LICENSE`, `README.md`, `resources/` (`icon.png`,
`settings.xml`, `language/`, `lib/`), excluding tests, docs, and bytecode.

## Troubleshooting

- **Categories or portal fail to open:** verify the Kodi device can reach the
  portal and configured node URLs over BDIX/LAN, or trigger **Refresh listing**.
- **Movie search misses results:** check node URL settings for nodes 7 and 14
  and inspect `[plugin.bdix.dhakaflix]` lines in `kodi.log`.
- **Playback fails:** verify the Kodi device itself can open the resolved video
  URL directly over HTTP.
- **Stale folder contents:** use **Refresh listing** in the folder's context
  menu, use **Clear listing cache**, or lower `cache_ttl`.
