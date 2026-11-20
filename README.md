# DhakaFlix Kodi Add-on

On-demand browser and movie search Kodi video add-on for DhakaFlix `h5ai`
directory servers. It navigates listings dynamically and plays original media
URLs directly in Kodi without downloading, proxying, or transcoding video files.

See [PROJECT-HANDOFF.md](PROJECT-HANDOFF.md) for architecture, performance
decisions, validation commands, and release checks.

## Requirements

- Kodi Omega 21.3 or later (Python add-on API 3.0.1; Python 3.11 runtime).
- The Kodi device must be able to reach the DhakaFlix portal and media nodes
  over your BDIX/LAN connection.

## Install in Kodi using the ZIP

### 1. Build the installable ZIP

From the project root, run:

```sh
python3 -m unittest discover -s tests -v
python3 tools/package.py
```

The package is created at:

```text
dist/plugin.bdix.dhakaflix-0.5.0.zip
```

Copy the ZIP to the Kodi device using a USB drive or a trusted local network
share. **Copy the ZIP itself; do not extract it.**

### 2. Allow installation from a ZIP

In Kodi, open **Settings → System → Add-ons** and enable **Unknown sources**.
Confirm Kodi's warning. Menu wording can vary slightly by Kodi version or skin.

### 3. Install the add-on

If you previously installed the old `plugin.video.dhakaflix` build, uninstall
it first from **Add-ons → My add-ons → Video add-ons → DhakaFlix → Uninstall**.
`plugin.bdix.dhakaflix` uses a distinct Kodi add-on ID, so Kodi will not
upgrade `plugin.video.dhakaflix` in place.

1. From Kodi's home screen, open **Add-ons**.
2. Select the **open-box/package icon** in the upper-left.
3. Choose **Install from zip file**.
4. If Kodi asks about an external source, confirm it.
5. Browse to the USB drive or network share and select
   `plugin.bdix.dhakaflix-0.5.0.zip`.
6. Wait for Kodi's **DhakaFlix add-on installed** notification.
7. Open **Add-ons → Video add-ons → DhakaFlix**.

If installing from a network share, first add it under **Settings → File
manager → Add source**, then select that source in step 5.

## Upgrading from v0.4.0

`v0.5.0` removes the background library sync service and `.strm` file
generator, returning the add-on to fast on-demand navigation and direct
playback. Installing `v0.5.0` upgrades over `v0.4.0` in place, and leaves any
previously generated `addon_data/plugin.bdix.dhakaflix/library/` files and
`sources.xml` entries untouched.

If you no longer want the old library folders from `v0.4.0`, you can remove
the **DhakaFlix Movies** and **DhakaFlix TV Shows** sources from Kodi's Video
sources menu (and optionally clean your Kodi video library and delete the
`library/` folder inside Kodi's `addon_data/plugin.bdix.dhakaflix/` directory).

## Features and settings

From the add-on home menu:

- **Browse DhakaFlix portal:** navigates directly from the fixed portal root
  (`http://172.16.50.7/`).
- **Movie categories:** curated shortcuts across configured movie nodes (nodes
  7 and 14).
- **TV & series categories:** curated shortcuts across configured series/show
  nodes (nodes 10, 12, and 14).
- **Search movies:** searches configured movie nodes concurrently and returns
  playable video results with an **Open parent folder** context menu action.
- **Clear listing cache:** clears cached directory listings from memory and
  SQLite (also cleans up any legacy `.json` cache files).
- **Add-on settings:** opens Kodi settings for **Discovery** (excluded
  keywords, listing cache duration, request timeout) and **DhakaFlix servers**
  (per-node base URLs; clear a node URL to disable its categories and search).

Inside any directory listing, the context menu provides **Refresh listing**
(invalidates that folder's cache and refreshes the container) and **Clear
listing cache**.

## Performance design

- `<reuselanguageinvoker>true</reuselanguageinvoker>` keeps the Python
  interpreter warm across folder clicks with per-request handle/context
  isolation.
- Persistent per-origin HTTP/1.1 keep-alive connection pooling and transparent
  `gzip`/`deflate` compression reduce latency over BDIX links.
- Two-tier caching (64-entry process LRU in RAM + single-file
  `listing_cache.sqlite`) with HTTP `ETag`/`Last-Modified` `304 Not Modified`
  revalidation avoids redundant HTML downloads and parses.
- Streaming HTML parsing, batch `addDirectoryItems` with `offscreen=True`, and
  Kodi 21 `InfoTagVideo` metadata keep large folders responsive.

## Install from a private GitHub repository

Push this project to your private GitHub repository. GitHub Actions runs Ruff
lint/format checks and unit tests across Linux (Python 3.11 and 3.15), Windows
(Python 3.15), and macOS (Python 3.15), then builds the ZIP artifact. Versioned
tags matching `plugin.bdix.dhakaflix/addon.xml` (for example `v0.5.0`) publish
the ZIP and its SHA-256 checksum as private release assets.

## Load the add-on source folder for development

Copy the **`plugin.bdix.dhakaflix` directory** directly inside your Kodi
`addons` directory:

```text
<Kodi data directory>/addons/plugin.bdix.dhakaflix/addon.xml
```

Common Kodi data locations include:

- **macOS:** `~/Library/Application Support/Kodi/addons/`
- **Windows:** `%APPDATA%\Kodi\addons\`
- **Linux:** `~/.kodi/addons/`

Quit Kodi before replacing add-on files, then restart it.

## Troubleshooting

- **Portal or categories do not load:** confirm the Kodi device can reach the
  portal and configured node addresses over your BDIX/LAN connection. Try
  **Refresh listing** from a folder's context menu.
- **Some movie search results are missing:** one or more movie nodes may be
  unreachable; check the configured node URLs and Kodi's log.
- **A video does not start:** confirm that the media-node URL is reachable
  directly from Kodi. Playback is not relayed through another computer.
- **Need the log:** use Kodi's **Settings → System → Logging** to enable debug
  logging, reproduce the issue, then check Kodi's `kodi.log` for
  `[plugin.bdix.dhakaflix]` entries.

## Build and test locally

```sh
python3 -m unittest discover -s tests -v
python3 tools/package.py
```

See [docs/](./docs/README.md) for design, development, and CI details.
