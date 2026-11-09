# Development and Testing

## Prerequisites

- Python 3 for parser and packaging utilities.
- Kodi installed on at least one target device for integration testing.
- Network access from the target Kodi device to the configured portal and
  direct media URLs.
- Kodi Matrix (19) or later; the add-on currently declares `xbmc.python`
  version `3.0.0`.

No separate HTTP server, database, Docker runtime, Go toolchain, or third-party
Python package is part of the planned add-on runtime.

## Suggested local workflow

1. Keep pure parsing and URL-resolution code separate from Kodi APIs.
2. Save representative portal responses as HTML fixtures, with private or
   user-specific data removed.
3. Run automated tests using the host Python interpreter:

   ```sh
   python3 -m unittest discover -s tests -v
   ```

4. Install the add-on directory into Kodi's `addons` directory for rapid
   development, then restart Kodi or reload add-ons.
5. Inspect Kodi's log after browsing, refreshing, and playing a sample item.
6. Build the installable ZIP:

   ```sh
   python3 tools/package.py
   ```

7. Test a clean ZIP installation on each declared
   target Kodi version.

## Private GitHub and CI/CD

Create an empty private GitHub repository, initialize and push this project as
described in the [repository README](../README.md), and enable GitHub Actions
for the repository. `.github/workflows/ci-cd.yml` runs the standard-library
test suite on Python 3.8, 3.11, and 3.14, validates the add-on metadata/package,
and uploads the installable ZIP as a workflow artifact.

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

### Integration tests

Use a local fixture HTTP server when possible to exercise fetching, status
handling, timeout behavior, cache expiry, and refresh without relying on the
live portal.

### Kodi manual checks

- Add-on installs and opens without a traceback.
- Home categories and nested folders are navigable with a remote/keyboard.
- Back navigation and refresh behave correctly.
- A sample video starts, seeks, and stops on supported clients.
- Missing artwork or subtitles does not prevent playback.
- An unavailable source produces a useful notification and Kodi log entry.

## Packaging

The final ZIP should contain one top-level add-on directory named to match the
add-on ID, including `addon.xml`, Python modules, resources, and artwork.
Exclude test fixtures, development notes, bytecode caches, and local settings.

Before release, verify the `addon.xml` version, Kodi Python API requirement,
license metadata, and artwork. Install the ZIP into a clean Kodi profile to
catch missing files or undeclared dependencies. This workspace has not yet been
validated in a real Kodi runtime or against the live private portal.

## Troubleshooting

- **Categories do not appear:** inspect the Kodi log, confirm the portal URL
  from the Kodi device, and compare the response with parser fixtures.
- **Folders show no entries:** verify the fetched page is an actual directory
  listing and not an error page or redirect target.
- **Playback fails after selecting a file:** open the resolved media URL from
  the Kodi device's network, verify its HTTP response and byte-range support,
  and check Kodi's player log.
- **Works on one device only:** confirm both devices can route to the same
  source node; direct playback is performed by the Kodi client.
- **Stale listing:** use the add-on refresh action or reduce the configured
  cache TTL.
