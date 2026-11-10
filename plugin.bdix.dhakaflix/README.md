# DhakaFlix for Kodi

An unofficial Kodi video add-on and background service. It discovers movie
and TV/series directories from the DhakaFlix portal and generates
local `.strm` pointers to original media URLs. It does not download, proxy, or
transcode media.

## Library synchronization

The service automatically syncs once on a fresh add-on profile. It retries an
unsuccessful or incomplete initial sync on later Kodi startups, up to three
total attempts. Add-on updates do not trigger automatic sync. After the initial
sync succeeds or retries are exhausted, use **Sync DhakaFlix library now** for
future syncs. Manual sync has no cooldown but will not overlap an active sync;
a successful manual sync satisfies the initial sync.

Generated Movies and TV Shows roots are stored below the add-on's Kodi profile.
The service registers them in Kodi's `sources.xml`, preserves other sources,
and backs up that file before changing it. If a Kodi restart is needed to load
the new paths, the add-on notifies you. After each complete sync, Kodi is asked
to scan both library roots.

Metadata scrapers remain under Kodi's control. The add-on does not run Kodi's
global video-library clean because that could remove unrelated missing items
from the profile. Kodi may retain stale library database entries for removed
`.strm` files until cleaned separately.

Movies remain in Movies. All media discovered in TV/series categories goes
under TV Shows, grouped by series and season where the portal structure allows.
Documentaries, wrestling, awards shows, games, software, and tutorials are
excluded from library sync. Distinct media URLs are retained, including
alternate encodes. Kodi fetches media bytes from the original node when
playing, so the Kodi device must reach each media node.

The next sync run at least seven days after the previous full crawl performs a
full audit, reclassifies existing entries, removes excluded media, and prunes
empty managed folders. This interval does not schedule a sync; after the
initial sync attempts, use **Sync DhakaFlix library now** to start one.

Library files and sync state are under the add-on profile's `library` folder
(`special://profile/addon_data/plugin.bdix.dhakaflix/library`). The media and
directory inventory is stored in `library.sqlite`; sync lifecycle status
remains in `state.json`. Initial and qualifying seven-day full crawls reconcile
the entire catalog; intervening syncs use h5ai directory dates to skip
unchanged subtrees. Since h5ai dates are minute-resolution and not recursive,
updates inside a skipped subtree may wait until a later full crawl. Crawls are
bounded by page, depth, media, category, duration, and response-size limits.
Detailed failures are shown in Kodi error dialogs.

## Directory browsing

The add-on retains generic portal browsing, curated movie/series categories,
and search. The portal root is fixed. Excluded keywords, listing cache
duration, request timeout, and individual node URLs are configurable in
**Discovery** settings. Node URL settings affect browsing, categories, and
search only; library sync uses its own fixed sources.

In the add-on's **Sync** settings, **Sync movies** is on by default and **Sync
TV series** is off. Turn on either or both to choose the categories included
in library sync. Turning a type off removes its managed `.strm` files after a
complete sync when other selected media remain. Discovery settings control
excluded keywords, listing cache duration, and request timeout.

## Development

From the repository root:

```sh
python3 -m unittest discover -s tests -v
python3 tools/package.py
```

See the repository's `docs/` directory for detailed design and test guidance,
and `PROJECT-HANDOFF.md` at the repository root for implementation context
and outstanding release checks.
