# DhakaFlix Kodi add-on and library sync

Sync DhakaFlix movies and TV episodes into Kodi's video library using local
`.strm` pointers to the original media URLs. The add-on also retains its
on-demand browser. It does not download, proxy, or transcode video content.

See [PROJECT-HANDOFF.md](PROJECT-HANDOFF.md) for architecture, behavior
decisions, validation commands, and outstanding release checks.

## Requirements

- Kodi Matrix (19) or later.
- The Kodi device must be able to reach the DhakaFlix portal and media nodes
  over your BDIX/LAN connection.
- Kodi must have writable profile storage for generated library pointers.

## Install in Kodi using the ZIP

### 1. Build the installable ZIP

On the computer where you downloaded this project, run from the project root:

```sh
python3 -m unittest discover -s tests -v
python3 tools/package.py
```

The package is created at:

```text
dist/plugin.bdix.dhakaflix-0.3.0.zip
```

Copy the ZIP to the Kodi device using a USB drive or a trusted local network
share. **Copy the ZIP itself; do not extract it.**

### 2. Allow installation from a ZIP

In Kodi, open **Settings → System → Add-ons** and enable **Unknown sources**.
Confirm Kodi's warning. The menu wording can vary slightly by Kodi version or
skin.

### 3. Install the add-on

If you previously installed the old `plugin.video.dhakaflix` build, uninstall
it first from **Add-ons → My add-ons → Video add-ons → DhakaFlix → Uninstall**.
The renamed `plugin.bdix.dhakaflix` is a new Kodi add-on identity, so Kodi will
not upgrade the old ID in place.

1. From Kodi's home screen, open **Add-ons**.
2. Select the **open-box/package icon** in the upper-left.
3. Choose **Install from zip file**.
4. If Kodi asks about an external source, confirm it.
5. Browse to the USB drive or network share and select
   `plugin.bdix.dhakaflix-0.3.0.zip`.
6. Wait for Kodi's **DhakaFlix add-on installed** notification.
7. Open **Add-ons → Video add-ons → DhakaFlix**.

If installing from a network share, first add it under **Settings → File
manager → Add source**, then select that source in step 5.

### 4. Complete first-time library setup

The add-on service automatically starts a full sync only for a fresh add-on
profile. It allows up to three attempts, retrying on later Kodi startups after
an unsuccessful or incomplete attempt. Updates do not trigger automatic
syncs. Once the initial sync completes successfully—or its retry limit is
exhausted—syncing is manual only. Manual sync has no cooldown, but is ignored
while another sync is running. A successful manual sync also satisfies the
fresh-install initial sync.

The initial sync discovers movie and TV/series categories from the portal,
generates local `.strm` files, creates and validates separate Movies and TV
Shows folders, then registers those paths in Kodi's `sources.xml`. A persistent
background progress dialog shows crawl activity.

When notified that a restart is required, restart Kodi so it loads the new
sources. Content types and scrapers still need a one-time Kodi GUI setup:

1. In Kodi, open **Videos → Files** and select **DhakaFlix Movies**.
2. Set its content to **Movies**, choose a scraper, enable the movie-folder
   option if appropriate, and enable recursive scanning for category folders.
3. Repeat for **DhakaFlix TV Shows**, setting its content to **TV Shows** and
   choosing a TV scraper.
4. Open add-on settings and enable **I have configured DhakaFlix Movies and TV
   Shows content** after configuring the source content/scrapers you want Kodi
   to scan.
5. From **Add-ons → Video add-ons → DhakaFlix**, choose **Sync DhakaFlix library
   now** for a manual sync.

After a complete sync, DhakaFlix automatically asks Kodi to scan the Movies
and TV Shows sources. The scan starts once the one-time content/scraper setup
has been confirmed in add-on settings.

Automatic source registration edits Kodi's `sources.xml`, although Kodi's
documentation recommends managing sources through its GUI. Before changing
an existing file, the add-on makes a `.dhakaflix.bak` backup and preserves
other sources. Kodi may need a restart to load registered paths; the add-on
will notify you. This behavior needs testing on each Kodi platform/version.

After a complete sync, stale `.strm` files owned by this add-on may be removed.
The add-on does not run Kodi's global video-library clean because it can remove
unrelated missing items from this profile. Kodi may retain stale database
entries for removed `.strm` files until the library is cleaned separately.

### 5. Review add-on settings

Open **Add-ons → My add-ons → Video add-ons → DhakaFlix → Configure**.

- Under **Kodi library**, **Sync movies** is enabled by default and **Sync TV
  series** is disabled. Enable either or both to choose what library sync
  includes. Disabling a type removes its managed `.strm` entries on a complete
  sync when other selected media remain; Kodi may retain stale database rows.
- The DhakaFlix portal and DHAKA-FLIX-7/10/12/14 server addresses are fixed
  for this add-on.
- **Excluded keywords**, cache duration, and request timeout can be adjusted
  here.

The sync discovers current movie and TV/series links from the portal homepage.
Documentaries, wrestling, awards shows, games, software, and tutorials are
excluded from library sync. Every item from a series category goes under
`TV Shows/<series>/<season>` when a season is available; movies go under
Movies. Alternate URLs are retained as separate `.strm` files.

Generated `.strm` pointers and the SQLite media/directory inventory are stored
under the add-on's Kodi profile. The initial sync traverses the entire catalog.
Later syncs use h5ai folder dates to skip unchanged subtrees, except that a
sync run at least seven days after the previous full crawl performs a full
audit. The seven-day interval does not schedule a sync: after the initial
attempts, start syncs manually. These minute-resolution dates are not
recursive, so deeper changes can wait until a later full audit. Unchanged
`.strm` files are not rewritten. Existing entries are reclassified, excluded
entries removed, and empty managed folders pruned during a full audit. Kodi
fetches video bytes directly from the original media node during playback, so
the Kodi device must reach that node.
The home menu also retains generic browsing, curated categories, and search.

## Install from a private GitHub repository

Push this project to your private GitHub repository as described in
[Use a private GitHub repository](#use-a-private-github-repository) below.
GitHub Actions runs tests and builds an artifact on pushes and pull requests.
Download the ZIP from the completed workflow's **Artifacts** section, or from
the **Assets** section of a versioned private release. GitHub requires you to
sign in and have repository access to download private artifacts/releases.
Transfer the downloaded ZIP to Kodi and follow the install steps above.

The release workflow publishes when you push a tag matching the version in
`plugin.bdix.dhakaflix/addon.xml`, for example `v0.3.0`. A private GitHub
release is a download location; it does not automatically install or update
the add-on on Kodi.

## Load the add-on source folder for development

You can install the unpacked add-on for development instead of building a ZIP.
Copy the **`plugin.bdix.dhakaflix` directory** so it is directly inside the
Kodi `addons` directory:

```text
<Kodi data directory>/addons/plugin.bdix.dhakaflix/addon.xml
```

Common Kodi data locations include:

- **macOS:** `~/Library/Application Support/Kodi/addons/`
- **Windows:** `%APPDATA%\Kodi\addons\`
- **Linux:** `~/.kodi/addons/`

Kodi's data location can differ for portable installs, Flatpak/Snap packages,
Android, and other distributions. For Kodi already running, quit Kodi before
replacing add-on files, then restart it. For normal use, ZIP installation is
recommended.

## Use a private GitHub repository

Create an empty private GitHub repository (do not pre-create a README or
license), then run these commands from the project root. Replace the example
remote with your repository's SSH or HTTPS URL.

```sh
git init -b main
git add .
git commit -m "Initial DhakaFlix Kodi add-on"
git remote add origin git@github.com:YOUR_ACCOUNT/YOUR_PRIVATE_REPOSITORY.git
git push -u origin main
```

After updating `version` in `plugin.bdix.dhakaflix/addon.xml`, commit and push
the change, then tag that exact version:

```sh
git tag v0.3.0
git push origin v0.3.0
```

## Troubleshooting

- **Portal or categories do not load:** confirm the Kodi device (not just your
  development computer) can reach the fixed `172.16.50.x` portal and server
  addresses over your BDIX/LAN connection. Try **Refresh listing** from a
  folder's context menu.
- **Some search results are missing:** one or more nodes may be unreachable;
  check connectivity to the fixed server addresses and Kodi's log.
- **A video does not start:** confirm that the media-node URL is reachable
  directly from Kodi. Playback is not relayed through another computer.
- **Need the log:** use Kodi's **Settings → System → Logging** to enable debug
  logging, reproduce the issue, then check Kodi's `kodi.log` for a
  `[plugin.bdix.dhakaflix]` entry. The log location varies by operating system
  and package.

## Build and test locally

```sh
python3 -m unittest discover -s tests -v
python3 tools/package.py
```

See [docs/](./docs/README.md) for design, development, and CI details.
