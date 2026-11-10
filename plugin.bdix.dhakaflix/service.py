#!/usr/bin/env python
"""Kodi service for scheduled and manual DhakaFlix library synchronization."""

import json
import os
import tempfile
import time
import traceback
from contextlib import suppress

import xbmc
import xbmcaddon
import xbmcgui
import xbmcvfs
from resources.lib.catalog import NODE_URLS, PORTAL_URL
from resources.lib.library import (
    SourceRegistrationError,
    ensure_library_paths,
    library_paths,
    register_sources,
)
from resources.lib.portal import PortalClient
from resources.lib.sync import (
    SyncCancelled,
    run_sync,
)

ADDON_ID = "plugin.bdix.dhakaflix"
ADDON = xbmcaddon.Addon(id=ADDON_ID)
POLL_SECONDS = 10
MAX_INITIAL_SYNC_ATTEMPTS = 3


def _notify(message):
    xbmcgui.Dialog().notification(ADDON.getAddonInfo("name"), message)


def _notify_error(message, details):
    xbmcgui.Dialog().ok(
        ADDON.getAddonInfo("name"),
        f"{message}\n\n{details}",
    )


def _profile_path():
    return xbmcvfs.translatePath(ADDON.getAddonInfo("profile"))


def _quarantine_state(path, reason):
    destination = path + ".corrupt"
    suffix = 1
    while os.path.exists(destination):
        destination = f"{path}.corrupt.{suffix}"
        suffix += 1
    try:
        os.replace(path, destination)
    except OSError as error:
        raise RuntimeError(f"Unable to quarantine corrupt sync state: {error}") from error
    _notify_error(
        "DhakaFlix sync state was invalid and has been reset",
        f"The previous state was preserved at {destination}. {reason}",
    )


def _read_state(library_dir):
    path = os.path.join(library_dir, "state.json")
    try:
        with open(path, encoding="utf-8") as source:
            value = json.load(source)
    except FileNotFoundError:
        return {}
    except (OSError, ValueError) as error:
        if isinstance(error, OSError):
            raise RuntimeError(f"Unable to read sync state: {error}") from error
        _quarantine_state(path, str(error))
        return {}
    if not isinstance(value, dict):
        _quarantine_state(path, "The state document was not a JSON object.")
        return {}
    return value


def _write_state(library_dir, state):
    os.makedirs(library_dir, exist_ok=True)
    path = os.path.join(library_dir, "state.json")
    descriptor, temporary_path = tempfile.mkstemp(
        prefix=".state-", suffix=".tmp", dir=library_dir
    )
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as target:
            json.dump(state, target, ensure_ascii=False, sort_keys=True)
            target.flush()
            os.fsync(target.fileno())
        os.replace(temporary_path, path)
        if os.name != "nt":
            with suppress(OSError):
                directory_descriptor = os.open(library_dir, os.O_RDONLY)
                try:
                    os.fsync(directory_descriptor)
                finally:
                    os.close(directory_descriptor)
    except Exception:
        with suppress(FileNotFoundError):
            os.remove(temporary_path)
        raise


def _request_library_scan(path):
    escaped_path = os.path.abspath(path).replace('"', "")
    xbmc.executebuiltin(f'UpdateLibrary(video,"{escaped_path}")')


def _start_next_scan(library_dir, state):
    refresh = state.get("library_refresh")
    if not isinstance(refresh, dict):
        return
    queue = refresh.get("queue")
    if not isinstance(queue, list):
        raise TypeError("Pending library scan queue is invalid")
    if queue:
        path = queue.pop(0)
        refresh["active"] = path
        state["library_refresh"] = refresh
        _write_state(library_dir, state)
        _request_library_scan(path)
        return
    refresh.pop("active", None)
    if refresh.get("clean"):
        refresh["clean"] = False
        state["library_refresh"] = refresh
        _write_state(library_dir, state)
    state.pop("library_refresh", None)
    _write_state(library_dir, state)
    _notify("DhakaFlix Kodi library refresh complete")


def _begin_library_refresh(library_dir, state, paths):
    state["library_refresh"] = {
        "queue": [paths["movies"], paths["tv"]],
        "clean": False,
    }
    _write_state(library_dir, state)
    _start_next_scan(library_dir, state)


class ServiceMonitor(xbmc.Monitor):
    def __init__(self):
        super().__init__()
        self.library_dir = library_paths(_profile_path())["state"]

    def onNotification(self, sender, method, data):
        if method not in (
            "VideoLibrary.OnScanFinished",
            "VideoLibrary.OnCleanFinished",
        ):
            return
        try:
            state = _read_state(self.library_dir)
            refresh = state.get("library_refresh")
            if not isinstance(refresh, dict):
                return
            if method == "VideoLibrary.OnScanFinished":
                if not refresh.get("active"):
                    return
                refresh.pop("active", None)
                state["library_refresh"] = refresh
                _write_state(self.library_dir, state)
                _start_next_scan(self.library_dir, state)
            else:
                if refresh.get("phase") != "clean":
                    return
                state.pop("library_refresh", None)
                _write_state(self.library_dir, state)
                _notify("DhakaFlix Kodi library refresh complete")
        except Exception as error:  # noqa: BLE001
            _notify_error(
                "Unable to advance Kodi library refresh",
                f"{error}\n\n{traceback.format_exc()}",
            )


def _client():
    try:
        timeout = int(ADDON.getSetting("request_timeout") or "20")
    except ValueError:
        timeout = 20
    timeout = min(max(timeout, 3), 60)
    excluded_keywords = tuple(
        word.strip().casefold()
        for word in ADDON.getSetting("excluded_keywords").split(",")
        if word.strip()
    )
    return PortalClient(
        origin_url=PORTAL_URL,
        excluded_keywords=excluded_keywords,
        timeout=timeout,
        user_agent=f"Kodi DhakaFlix plugin/{ADDON.getAddonInfo('version')}",
        warning_callback=_notify,
        allowed_origins=(PORTAL_URL, *NODE_URLS.values()),
    )


def _content_is_configured():
    return ADDON.getSetting("library_sources_configured").casefold() == "true"


def _sync_media_types():
    media_types = set()
    if ADDON.getSetting("sync_movies").casefold() != "false":
        media_types.add("movie")
    if ADDON.getSetting("sync_series").casefold() == "true":
        media_types.add("series")
    if not media_types:
        raise ValueError("Enable movie sync, TV series sync, or both in add-on settings")
    return frozenset(media_types)


def _queue_refresh_until_configured(library_dir, state):
    state["library_refresh_pending"] = True
    state.pop("library_refresh_pending_clean", None)
    _write_state(library_dir, state)


def _resume_pending_refresh(library_dir, paths, resume_existing=False):
    if not _content_is_configured():
        return
    state = _read_state(library_dir)
    refresh = state.get("library_refresh")
    if isinstance(refresh, dict):
        if not resume_existing:
            return
        if refresh.get("phase") == "clean":
            state.pop("library_refresh", None)
            _write_state(library_dir, state)
            _notify("DhakaFlix sources refreshed; Kodi-wide library cleaning was skipped")
            return
        if refresh.get("active"):
            refresh.setdefault("queue", []).insert(0, refresh.pop("active"))
            state["library_refresh"] = refresh
            _write_state(library_dir, state)
        _start_next_scan(library_dir, state)
        return
    if not state.pop("library_refresh_pending", False):
        return
    state.pop("library_refresh_pending_clean", None)
    _write_state(library_dir, state)
    _begin_library_refresh(library_dir, state, paths)


def _ensure_sources(library_dir, paths, state):
    ensure_library_paths(paths)
    sources_path = xbmcvfs.translatePath("special://profile/sources.xml")
    changed = register_sources(sources_path, paths["movies"], paths["tv"])
    if changed:
        state["registration_restart_pending"] = True
        _write_state(library_dir, state)
        _notify(
            "DhakaFlix library paths registered. Restart Kodi to load them, "
            "then set Movies and TV Shows content/scrapers once."
        )
        return True
    if state.pop("registration_restart_pending", False):
        _queue_refresh_until_configured(library_dir, state)
        _notify(
            "DhakaFlix sources are loaded. Confirm their Movies and TV Shows "
            "content/scraper settings in Kodi."
        )
    return False


def _perform_sync(
    library_dir,
    paths,
    client,
    monitor,
    progress_dialog,
    media_types=None,
):
    state = _read_state(library_dir)
    registration_changed = bool(state.get("registration_restart_pending"))
    if not registration_changed:
        try:
            registration_changed = _ensure_sources(library_dir, paths, state)
        except SourceRegistrationError as error:
            _notify_error("Unable to register library paths", str(error))
            return False
        state = _read_state(library_dir)
        registration_changed = registration_changed or bool(
            state.get("registration_restart_pending")
        )
    _notify("DhakaFlix library sync started")
    last_saved_progress = [0]
    progress_error_reported = [False]

    def progress(page_count, media_count, category):
        message = (
            f"Scanning {category}\n"
            f"{page_count} pages visited; {media_count} videos found "
            "in scanned folders"
        )
        try:
            progress_dialog.update(0, "DhakaFlix library sync", message)
        except Exception as error:  # noqa: BLE001
            if not progress_error_reported[0]:
                progress_error_reported[0] = True
                _notify_error("Unable to update sync progress", str(error))
        if page_count - last_saved_progress[0] >= 50:
            last_saved_progress[0] = page_count
            state = _read_state(library_dir)
            sync_status = state.get("sync_status", {})
            if isinstance(sync_status, dict) and sync_status.get("status") == "running":
                sync_status.update(
                    page_count=page_count,
                    media_count=media_count,
                    category=category,
                )
                state["sync_status"] = sync_status
                _write_state(library_dir, state)

    try:
        result = run_sync(
            client,
            library_dir,
            cancelled=monitor.abortRequested,
            progress=progress,
            media_types=(
                media_types if media_types is not None else _sync_media_types()
            ),
        )
    except SyncCancelled:
        _notify("DhakaFlix sync stopped because Kodi is shutting down")
        return False
    except Exception as error:  # noqa: BLE001
        _notify_error(
            "DhakaFlix sync failed",
            f"{error}\n\n{traceback.format_exc()}",
        )
        return False

    if not result.complete:
        details = "\n".join(result.errors[:10])
        if len(result.errors) > 10:
            details += f"\n...and {len(result.errors) - 10} more errors"
        _notify_error(
            f"DhakaFlix sync incomplete after {result.page_count} pages; "
            "nothing was pruned",
            details or "The portal crawl did not find any supported videos.",
        )
        return False

    if registration_changed:
        _notify("Sync complete; restart Kodi before scanning the new sources")
        return True
    try:
        if _content_is_configured():
            _begin_library_refresh(library_dir, _read_state(library_dir), paths)
        else:
            _queue_refresh_until_configured(library_dir, _read_state(library_dir))
            _notify("Set the DhakaFlix source content and scraper in Kodi to finish")
    except Exception as error:  # noqa: BLE001
        _report_service_error(
            "DhakaFlix sync succeeded but Kodi library refresh could not be queued",
            error,
        )
    _notify(
        f"DhakaFlix sync complete: {result.movie_count} movies and "
        f"{result.episode_count} episodes"
    )
    return True


def _report_service_error(message, error):
    _notify_error(message, f"{error}\n\n{traceback.format_exc()}")


def _finish_sync_status(library_dir, started_at, succeeded, automatic):
    state = _read_state(library_dir)
    status = "complete" if succeeded else "failed"
    state["sync_status"] = {
        "status": status,
        "started_at": started_at,
        "finished_at": time.time(),
    }
    if succeeded:
        auto_sync = state.get("auto_sync")
        if not isinstance(auto_sync, dict):
            auto_sync = {"attempts": 0}
        auto_sync.update(status="complete", completed_at=time.time())
        state["auto_sync"] = auto_sync
    elif automatic:
        auto_sync = state["auto_sync"]
        attempts = auto_sync["attempts"]
        if attempts >= MAX_INITIAL_SYNC_ATTEMPTS:
            auto_sync.update(status="exhausted", last_failure_at=time.time())
            _notify(
                "Initial DhakaFlix sync could not complete after 3 attempts; "
                "use manual sync to retry"
            )
        else:
            auto_sync.update(status="retry_pending", last_failure_at=time.time())
    _write_state(library_dir, state)


def _initialize_auto_sync(library_dir):
    state_path = os.path.join(library_dir, "state.json")
    state_exists = os.path.isfile(state_path)
    state = _read_state(library_dir)
    if "auto_sync" not in state:
        state["auto_sync"] = {
            "attempts": 0,
            "status": "pending" if not state_exists else "disabled",
        }
        _write_state(library_dir, state)
    auto_sync = state["auto_sync"]
    if (
        not isinstance(auto_sync, dict)
        or not isinstance(auto_sync.get("attempts"), int)
        or auto_sync["attempts"] < 0
        or auto_sync.get("status")
        not in (
            "pending",
            "retry_pending",
            "running",
            "complete",
            "exhausted",
            "disabled",
        )
    ):
        raise ValueError("Persisted automatic sync state is invalid")
    return state


def _auto_sync_due(state):
    auto_sync = state.get("auto_sync")
    return (
        isinstance(auto_sync, dict)
        and auto_sync.get("status") in ("pending", "retry_pending")
        and auto_sync.get("attempts", MAX_INITIAL_SYNC_ATTEMPTS)
        < MAX_INITIAL_SYNC_ATTEMPTS
    )


def _recover_interrupted_sync(library_dir):
    state = _read_state(library_dir)
    sync_status = state.get("sync_status")
    if isinstance(sync_status, dict) and sync_status.get("status") == "running":
        sync_status.update(
            status="interrupted",
            finished_at=time.time(),
        )
        state["sync_status"] = sync_status
        auto_sync = state.get("auto_sync")
        if isinstance(auto_sync, dict) and auto_sync.get("status") == "running":
            if auto_sync.get("attempts", 0) >= MAX_INITIAL_SYNC_ATTEMPTS:
                auto_sync.update(status="exhausted", last_failure_at=time.time())
                _notify(
                    "Initial DhakaFlix sync was interrupted after its final "
                    "automatic attempt; use manual sync to retry"
                )
            else:
                auto_sync.update(status="retry_pending", last_failure_at=time.time())
            state["auto_sync"] = auto_sync
        _write_state(library_dir, state)
        _notify("Recovered an interrupted DhakaFlix sync")


def _run_sync_request(library_dir, paths, monitor, automatic=False):
    progress_dialog = None
    sync_claimed = False
    started_at = None
    succeeded = False
    try:
        state = _read_state(library_dir)
        sync_status = state.get("sync_status", {})
        if isinstance(sync_status, dict) and sync_status.get("status") == "running":
            _notify("DhakaFlix sync is already running")
            return
        if automatic and not _auto_sync_due(state):
            return
        media_types = _sync_media_types()
        started_at = time.time()
        if automatic:
            auto_sync = state["auto_sync"]
            auto_sync.update(
                status="running",
                attempts=auto_sync["attempts"] + 1,
                started_at=started_at,
            )
            state["auto_sync"] = auto_sync
        state["sync_status"] = {
            "status": "running",
            "started_at": started_at,
            "page_count": 0,
            "media_count": 0,
        }
        _write_state(library_dir, state)
        sync_claimed = True
        progress_dialog = xbmcgui.DialogProgressBG()
        progress_dialog.create("DhakaFlix library sync", "Preparing catalog sync")
        succeeded = _perform_sync(
            library_dir, paths, _client(), monitor, progress_dialog, media_types
        )
    except Exception as error:  # noqa: BLE001
        _report_service_error("DhakaFlix sync request failed", error)
    finally:
        if progress_dialog is not None:
            try:
                progress_dialog.close()
            except Exception as error:  # noqa: BLE001
                _report_service_error(
                    "Unable to close sync progress dialog",
                    error,
                )
        if sync_claimed:
            try:
                _finish_sync_status(library_dir, started_at, succeeded, automatic)
            except Exception as error:  # noqa: BLE001
                _report_service_error(
                    "Unable to save DhakaFlix sync status",
                    error,
                )


def _recover_sync_request(library_dir):
    request_path = os.path.join(library_dir, "sync.request")
    processing_path = request_path + ".processing"
    if not os.path.isfile(processing_path):
        return
    if os.path.exists(request_path):
        os.remove(processing_path)
    else:
        os.replace(processing_path, request_path)


def _process_sync_request(library_dir, paths, monitor):
    request_path = os.path.join(library_dir, "sync.request")
    processing_path = request_path + ".processing"
    if not os.path.isfile(request_path):
        return
    try:
        os.replace(request_path, processing_path)
    except OSError as error:
        _notify_error("Unable to claim manual sync request", str(error))
        return
    _run_sync_request(library_dir, paths, monitor)
    try:
        if monitor.abortRequested() and not os.path.exists(request_path):
            os.replace(processing_path, request_path)
        else:
            os.remove(processing_path)
    except FileNotFoundError:
        pass
    except OSError as error:
        _notify_error("Unable to finalize manual sync request", str(error))


def main():
    try:
        profile_dir = _profile_path()
        paths = library_paths(profile_dir)
        library_dir = paths["state"]
        os.makedirs(library_dir, exist_ok=True)
        monitor = ServiceMonitor()
        _recover_sync_request(library_dir)
        _recover_interrupted_sync(library_dir)
        state = _initialize_auto_sync(library_dir)
        try:
            _ensure_sources(library_dir, paths, state)
        except SourceRegistrationError as error:
            _notify_error("Unable to register library paths", str(error))
        else:
            try:
                _resume_pending_refresh(library_dir, paths, resume_existing=True)
            except Exception as error:
                _report_service_error("Unable to resume Kodi library refresh", error)
            state = _read_state(library_dir)
            if (
                not state.get("library_refresh")
                and not state.get("library_refresh_pending")
                and _auto_sync_due(state)
            ):
                _run_sync_request(library_dir, paths, monitor, automatic=True)
    except Exception as error:
        _report_service_error("DhakaFlix service initialization failed", error)
        return

    while not monitor.abortRequested():
        if monitor.waitForAbort(POLL_SECONDS):
            break
        try:
            _resume_pending_refresh(library_dir, paths)
            state = _read_state(library_dir)
            request_path = os.path.join(library_dir, "sync.request")
            if os.path.isfile(request_path) and not state.get("library_refresh"):
                _process_sync_request(library_dir, paths, monitor)
        except Exception as error:
            _report_service_error("DhakaFlix service iteration failed", error)


if __name__ == "__main__":
    main()
