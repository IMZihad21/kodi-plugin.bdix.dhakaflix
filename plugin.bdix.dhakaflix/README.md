# DhakaFlix for Kodi

An unofficial Kodi video add-on for browsing directory listings that are
reachable from your Kodi device and playing supported video files directly.
It does not run a server, proxy media, or transcode video.

## Requirements

- Kodi Matrix (19) or later with the `xbmc.python` 3.0.0 API.
- Network access from Kodi to the configured portal and the selected media
  host. Private BDIX addresses must be reachable from the Kodi device itself.

## Install

Build `dist/plugin.bdix.dhakaflix-0.1.0.zip` using the repository packaging
command, copy the ZIP to Kodi, then use **Add-ons → Install from zip file**.
The first install may require enabling installation from unknown sources in
Kodi settings.

## Configure

The home menu includes generic portal browsing, curated movie and series
categories, and search across relevant nodes. In add-on settings, configure
the portal start URL, node URLs, excluded category words, listing cache
duration, and network timeout. Leave a node URL blank to disable its category
shortcuts and search. Select a video to have Kodi play its original URL.

The add-on only requests directory pages and searches when selected by the
user. Video bytes are fetched by Kodi's player directly from the source. It
stores parsed listings in Kodi's add-on profile cache; it does not collect
accounts or passwords.

## Development

From the repository root:

```sh
python3 -m unittest discover -s tests -v
python3 tools/package.py
```

See the repository's `docs/` directory for design, test, and release guidance.
