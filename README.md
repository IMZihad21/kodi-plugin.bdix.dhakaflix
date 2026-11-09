# DhakaFlix Kodi add-on

Browse DhakaFlix directory listings and play the original video URLs directly
in Kodi. This add-on runs inside Kodi: it does not need a separate server,
proxy, account, or transcoder.

## Requirements

- Kodi Matrix (19) or later.
- The Kodi device must be able to reach the DhakaFlix portal and media nodes
  over your BDIX/LAN connection.

## Install in Kodi using the ZIP

### 1. Build the installable ZIP

On the computer where you downloaded this project, run from the project root:

```sh
python3 -m unittest discover -s tests -v
python3 tools/package.py
```

The package is created at:

```text
dist/plugin.bdix.dhakaflix-0.1.0.zip
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
   `plugin.bdix.dhakaflix-0.1.0.zip`.
6. Wait for Kodi's **DhakaFlix add-on installed** notification.
7. Open **Add-ons → Video add-ons → DhakaFlix**.

If installing from a network share, first add it under **Settings → File
manager → Add source**, then select that source in step 5.

### 4. Configure the portal and nodes

Open **Add-ons → My add-ons → Video add-ons → DhakaFlix → Configure**.

- **Portal start URL** is used by **Browse configured portal**. The default is
  `http://172.16.50.7/`.
- **DhakaFlix node 7/9/12/14 URL** settings power the curated category menus
  and searches. Defaults are `http://172.16.50.7/`,
  `http://172.16.50.9/`, `http://172.16.50.12/`, and
  `http://172.16.50.14/`. Change a URL if your BDIX provider uses different
  addresses; leave it blank to disable that node.
- **Excluded keywords**, cache duration, and request timeout can also be
  adjusted here.

The add-on home screen offers generic portal browsing, movie categories,
series categories, movie search, and series search. Kodi's player fetches
selected video files directly from the source. Search requests run only when
you submit a search; directory pages are fetched as you browse.

## Install from a private GitHub repository

Push this project to your private GitHub repository as described in
[Use a private GitHub repository](#use-a-private-github-repository) below.
GitHub Actions runs tests and builds an artifact on pushes and pull requests.
Download the ZIP from the completed workflow's **Artifacts** section, or from
the **Assets** section of a versioned private release. GitHub requires you to
sign in and have repository access to download private artifacts/releases.
Transfer the downloaded ZIP to Kodi and follow the install steps above.

The release workflow publishes when you push a tag matching the version in
`plugin.bdix.dhakaflix/addon.xml`, for example `v0.1.0`. A private GitHub
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
git tag v0.1.0
git push origin v0.1.0
```

## Troubleshooting

- **Portal or categories do not load:** confirm the Kodi device (not just your
  development computer) can open the configured `172.16.50.x` URLs over your
  BDIX/LAN connection. Check the node URL settings and try **Refresh listing**
  from a folder's context menu.
- **Some search results are missing:** one or more nodes may be unreachable;
  check each node URL and Kodi's log.
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
