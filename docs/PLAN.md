# DhakaFlix Kodi Add-on Plan

## 1. Goal

Build a Kodi video add-on for browsing and playing media published in DhakaFlix's
`h5ai` directory listings. Kodi is the client and the integration: this project
will not run a separate server or imitate the Jellyfin REST API.

The add-on provides curated movie/series entry points and on-demand multi-node
search, alongside generic directory browsing. It opens original media URLs in
Kodi. It does not synchronize watch state or proxy/transcode media.

## 2. Source characteristics

The portal has been observed across several private-network nodes:

- `172.16.50.7`: English 720p, Kolkata Bangla, foreign-language, Satyajit Ray,
  and 3D movies.
- `172.16.50.14`: English 1080p, Hindi, South Indian, animation, IMDb Top 250,
  and Korean series.
- `172.16.50.12`: TV and web series in alphabet buckets.
- `172.16.50.10`: anime, cartoons, documentaries, wrestling, awards, and shows.

These addresses and directory names can change. Keep category paths curated for
useful navigation, but make each node URL configurable and allow a node to be
disabled by clearing its setting. Keep the generic browser rooted at the fixed
portal homepage; do not expose a portal-root setting.

Directory pages use `h5ai` table rows with `fb-i`, `fb-n`, `fb-d`, and `fb-s`
cells. Folder/file icons distinguish directories from files. Common media
layouts include year buckets and title folders for movies, alphabet buckets and
season folders for shows, plus video files, image artwork, and subtitles.

## 3. Product and technical decisions

1. **Kodi add-on, not a server.** Use Kodi's Python add-on interfaces and let
   Kodi own navigation, playback, UI, and watched state.
2. **Browse on demand.** Fetch a page when a user opens it; do not recursively
   crawl the entire portal at add-on startup.
3. **Direct playback first.** Pass the selected origin video URL to Kodi. Do
   not build a proxy or transcode path unless a real client/network limitation
   demonstrates the need.
4. **Curate, configure, and discover.** Provide useful node-specific category
   shortcuts and search nodes; expose node base URLs in settings, and retain
   generic portal browsing for new or changed paths.
5. **Small, explicit cache.** Cache fetched directory pages with a configurable
   TTL. Provide a user-triggered refresh action that bypasses or invalidates
   the relevant cached page.
6. **No account credentials.** The source portal is treated as unauthenticated
   unless that changes. Do not collect or persist Kodi credentials.
7. **No third-party Python packages initially.** Prefer Python's standard
   library and Kodi's bundled APIs to keep installation self-contained.
8. **Respect network boundaries.** The portal and configured nodes may only be
   reachable on the ISP/LAN network. The add-on must report connection failures clearly;
   do not imply that private-origin URLs will work from arbitrary networks.

## 4. Add-on structure

```text
plugin.bdix.dhakaflix/
├── addon.xml
├── default.py
├── resources/
│   ├── settings.xml
│   ├── language/
│   │   └── resource.language.en_gb/
│   │       └── strings.po
│   └── lib/
│       ├── portal.py
├── catalog.py
│       ├── parser.py
│       ├── router.py
│       ├── urls.py
│       └── cache.py
└── README.md
```

Keep the Kodi entry point thin. Put URL fetching and HTML parsing outside the
Kodi UI adapter so those parts can be tested independently.

## 5. User experience and routing

- Add-on home: offer generic portal browsing, curated movie/series categories,
  multi-node search, and settings.
- Node URLs, exclusions, cache, and request timeout are configurable from Kodi
  settings. The portal root remains fixed.
- Category or folder: show child folders and supported media files.
- Video item: display a playable list item with a sensible title, playable
  content type, and available artwork.
- TV folders: show seasons and episodes when the source structure supports
  reliable identification; otherwise preserve the directory hierarchy.
- Context menu: offer refresh for the current listing.
- Errors: show a Kodi notification/dialog for unreachable pages, invalid
  responses, or empty results, with enough context to troubleshoot.

Use opaque, URL-encoded plugin route parameters. Never concatenate untrusted
link text directly into a route. Normalize and validate resolved HTTP(S) links
before requesting or playing them.

## 6. Implementation phases

### Phase 0: establish the target — complete

- Target Kodi Omega 21.3 or later through the `xbmc.python` 3.0.1 API and
  Python 3.11-compatible syntax.
- Keep target device/operating-system playback validation as a release gate.
- Verify that the Kodi device can reach the fixed portal and a sample
  media URL.
- Capture representative category, folder, movie, and TV HTML fixtures without
  including private user data.

### Phase 1: minimal playable vertical slice — implemented, Kodi validation pending

- Add-on metadata, fixed-root discovery, folder navigation, direct
  playback routing, and visible/logged network failures are implemented.
- Validate the vertical slice in a real Kodi installation on the intended
  device and on a second platform before release.

### Phase 2: robust browsing — implemented, portal validation pending

- Handle observed `h5ai` rows and generic anchor pages, relative/escaped URLs,
  parent links, image/subtitle files, and common video extensions.
- Configure exclusions, cache TTL, and network timeout in Kodi settings.
- Refresh the current listing through a context action and use deterministic
  folder-first sorting.
- Verify actual DhakaFlix pages and every discovered node from Kodi; fixtures
  are not a substitute for this network validation.

### Phase 3: TV, artwork, and subtitles — initial support implemented

- Preserve season/episode navigation through the source folder hierarchy.
- Prefer image filenames that suggest a poster or cover when a listing offers
  artwork.
- Attach adjacent same-name subtitle files to playable list items. Verify this
  behavior with the target Kodi player and source formats.

### Phase 4: multi-node catalog and search — implemented, live portal validation pending

- Curated categories use observed node paths, while node base URLs remain
  editable and blank settings disable individual nodes.
- Search runs concurrently across relevant movie or series nodes, filters to
  supported video links, deduplicates results, and reports unreachable sources
  without hiding successful results.
- Validate current category paths, search API responses, and node reachability
  from the actual Kodi network.

### Phase 5: packaging and release — local ZIP implemented, publication pending

- Build and inspect the installable ZIP locally; clean-install and test it on
  each declared Kodi target.
- Document installation, settings, known network requirements, and logs.
- Tag a version and publish checksums alongside the ZIP.

## 7. Acceptance criteria

- The add-on installs from a ZIP on the declared Kodi versions.
- Home offers generic browsing as well as curated movie and series categories.
- Each node can be reconfigured or disabled without editing source code.
- Search returns playable videos from configured relevant nodes and degrades
  visibly when one or more sources are unreachable.
- Folder navigation handles links containing spaces, parentheses, brackets,
  and non-ASCII names.
- Selecting a supported video opens Kodi playback at the source URL.
- A broken or unreachable origin fails visibly rather than appearing as a
  successful empty library.
- Refresh reloads the current page; ordinary browsing uses the configured TTL.
- No login/password prompt, background service, external server, or third-party
  Python dependency is required.

## 8. Current implementation and validation

The add-on, settings, standard-library parser/cache/network modules, local
fixture-based tests, and ZIP builder are in the repository. Run:

```sh
python3 -m unittest discover -s tests -v
python3 tools/package.py
```

Host-side automated tests do not prove compatibility with the actual Kodi GUI
or player. A real Kodi install and access to the private BDIX portal are
required before calling the release production-ready.

## 9. Previous prototype

The earlier Go/Jellyfin server was removed. Its API emulation, user/session
store, and server deployment plan are obsolete. Reuse only source-independent
observations and parser ideas after verifying them against current portal HTML.

## 10. Related documents

- [Kodi add-on design](./KODI-ADDON.md)
- [Development and testing guide](./DEVELOPMENT.md)
- [Documentation index](./README.md)
