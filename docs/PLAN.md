# DhakaFlix Kodi Add-on Plan

## 1. Goal

Build a fast, lightweight Kodi video add-on (`plugin.bdix.dhakaflix`) for
on-demand browsing, movie search, and direct playback of media published on
DhakaFlix's `h5ai` HTTP directory servers. Kodi owns the UI, navigation,
favourites, playback, and watched state; the add-on runs no background service,
external server, `.strm` generator, proxy, or transcoder.

## 2. Source characteristics

DhakaFlix content is organized across private-network BDIX nodes:

- `172.16.50.7` (`DHAKA-FLIX-7`): portal root, English 720p, Kolkata Bangla,
  foreign-language, and 3D movies.
- `172.16.50.14` (`DHAKA-FLIX-14`): English 1080p, Hindi, South Indian,
  animation, IMDb Top 250 movies, and Korean series.
- `172.16.50.12` (`DHAKA-FLIX-12`): TV and web series.
- `172.16.50.10` (`DHAKA-FLIX-10`): anime, cartoons, documentaries, wrestling,
  awards, and shows.

Node base URLs are configurable in Kodi settings (and blanking a node disables
its categories and movie search), while generic browsing stays rooted at the
fixed portal homepage (`http://172.16.50.7/`).

Directory pages use `h5ai` tables (`fb-i`, `fb-n`, `fb-d`, `fb-s`) with folder
icons, names, modification timestamps, and file sizes. Search uses the `h5ai`
JSON POST API against movie nodes (`7` and `14`).

## 3. Product and technical decisions

1. **On-demand navigation only:** Fetch listings only when opened; keep no
   background service, local `.strm` tree, or separate database inventory.
2. **Direct playback:** Pass resolved HTTP(S) media URLs straight to Kodi's
   player via `setResolvedUrl`, attaching cached adjacent subtitles, artwork,
   and `InfoTagVideo` metadata.
3. **Movie-only search + curated browsing:** Provide generic portal browsing,
   curated movie and TV/series categories, and concurrent movie search with an
   **Open parent folder** context action.
4. **Minimal home menu:** Rely on Kodi's built-in Favourites rather than custom
   add-on bookmarks or search/playback history.
5. **Performance-first runtime:**
   - Enable `<reuselanguageinvoker>true</reuselanguageinvoker>` with per-call
     request contexts and shared thread-safe HTTP/cache state.
   - Reuse per-origin HTTP/1.1 keep-alive connections with `gzip`/`deflate`
     compression and streaming HTML parsing.
   - Use a two-tier cache (in-memory LRU + bounded `listing_cache.sqlite`) with
     conditional `ETag`/`Last-Modified` `304 Not Modified` revalidation.
   - Batch `offscreen=True` `ListItem` delivery with `addDirectoryItems`.
6. **Zero third-party dependencies:** Rely exclusively on Python 3.11's
   standard library and Kodi's `xbmc.python` 3.0.1 API.

## 4. Add-on structure

```text
plugin.bdix.dhakaflix/
├── LICENSE
├── README.md
├── addon.xml
├── default.py
└── resources/
    ├── __init__.py
    ├── icon.png
    ├── settings.xml
    ├── language/
    │   └── resource.language.en_gb/
    │       └── strings.po
    └── lib/
        ├── __init__.py
        ├── cache.py
        ├── catalog.py
        ├── config.py
        ├── parser.py
        ├── portal.py
        ├── router.py
        └── urls.py
```

## 5. Implementation status

- **Core navigation, movie search, and direct playback:** implemented in
  `v0.5.0` with `<reuselanguageinvoker>`, HTTP/1.1 keep-alive pooling,
  `gzip`/`deflate`, streaming `h5ai` parsing, LRU + SQLite caching with `304`
  revalidation, Kodi 21 `InfoTagVideo` metadata, and unit + local HTTP tests.
- **Kodi device & live BDIX portal verification:** pending testing on a real
  Kodi Omega 21.3+ installation connected to the BDIX portal.

## 6. Related documents

- [Kodi add-on design](./KODI-ADDON.md)
- [Development and testing guide](./DEVELOPMENT.md)
- [Documentation index](./README.md)
