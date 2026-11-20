# DhakaFlix for Kodi

An unofficial Kodi video add-on for browsing DhakaFlix `h5ai` listings,
searching movies across configured nodes, and playing original media URLs
directly in Kodi. It does not download, proxy, or transcode media, and does not
create `.strm` library files or run a background service.

## Features

- **Generic portal browser:** browse from the fixed DhakaFlix portal root
  (`http://172.16.50.7/`).
- **Curated categories:** shortcuts for Movies (nodes 7 and 14) and TV & series
  (nodes 10, 12, and 14).
- **Movie search:** concurrent search across configured movie nodes with an
  **Open parent folder** context menu action on each result.
- **Direct playback:** streams the resolved media URL directly to Kodi with
  Kodi 21 `InfoTagVideo` metadata, file sizes, adjacent matching subtitles, and
  poster/cover artwork from the cached listing.
- **Fast navigation & caching:** `<reuselanguageinvoker>`, HTTP/1.1 keep-alive
  pooling, `gzip`/`deflate` compression, streaming HTML parsing, and a two-tier
  RAM + SQLite listing cache (`listing_cache.sqlite`) supporting HTTP `304 Not
  Modified` revalidation. Use **Refresh listing** or **Clear listing cache** to
  invalidate cached pages.

## Settings

Open **Settings** for the add-on to configure:

- **Discovery:** excluded category keywords, listing cache duration (seconds;
  `0` disables caching), and request timeout (seconds).
- **DhakaFlix servers:** base URLs for nodes 7, 10, 12, and 14. Clear any node
  URL to disable its categories and movie search.

## Development

From the repository root:

```sh
python3 -m unittest discover -s tests -v
python3 tools/package.py
```

See the repository's `docs/` directory for detailed design and test guidance,
and `PROJECT-HANDOFF.md` at the repository root for architecture context and
release validation notes.
