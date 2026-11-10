import importlib.util
import sys
import tempfile
import time
import types
import unittest
from pathlib import Path
from unittest import mock

ADDON_DIR = Path(__file__).resolve().parents[1] / "plugin.bdix.dhakaflix"
sys.path.insert(0, str(ADDON_DIR))

from resources.lib.sync import SyncResult


class _Addon:
    def __init__(self, id):
        self.id = id

    def getAddonInfo(self, name):
        return {
            "name": "DhakaFlix",
            "version": "0.3.0",
            "profile": "profile",
        }[name]

    def getSetting(self, name):
        return ""


class _Monitor:
    def abortRequested(self):
        return False


class _ProgressDialog:
    def __init__(self):
        self.updates = []
        self.closed = False

    def create(self, *args):
        pass

    def update(self, *args):
        self.updates.append(args)

    def close(self):
        self.closed = True


class ServiceTests(unittest.TestCase):
    def setUp(self):
        self.notifications = []
        self.error_dialogs = []
        self.logs = []
        xbmc = types.ModuleType("xbmc")
        xbmc.Monitor = _Monitor
        xbmc.LOGINFO = 1
        xbmc.LOGWARNING = 2
        xbmc.LOGERROR = 3
        xbmc.log = lambda message, level=1: self.logs.append((message, level))
        self.builtins = []
        xbmc.executebuiltin = lambda *args: self.builtins.append(args[0])
        xbmcaddon = types.ModuleType("xbmcaddon")
        xbmcaddon.Addon = _Addon
        xbmcgui = types.ModuleType("xbmcgui")
        xbmcgui.Dialog = lambda: types.SimpleNamespace(
            notification=lambda *args: self.notifications.append(args),
            ok=lambda *args: self.error_dialogs.append(args),
        )
        self.progress_dialogs = []

        def create_progress_dialog():
            dialog = _ProgressDialog()
            self.progress_dialogs.append(dialog)
            return dialog

        xbmcgui.DialogProgressBG = create_progress_dialog
        xbmcvfs = types.ModuleType("xbmcvfs")
        xbmcvfs.translatePath = lambda path: path
        modules = {
            "xbmc": xbmc,
            "xbmcaddon": xbmcaddon,
            "xbmcgui": xbmcgui,
            "xbmcvfs": xbmcvfs,
        }
        self.module_patcher = mock.patch.dict(sys.modules, modules)
        self.module_patcher.start()
        self.addCleanup(self.module_patcher.stop)

        service_path = ADDON_DIR / "service.py"
        spec = importlib.util.spec_from_file_location(
            "dhakaflix_test_service", service_path
        )
        self.service = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.service)

    def test_service_uses_the_fixed_portal_url(self):
        client = self.service._client()
        self.assertEqual(client.origin_url, "http://172.16.50.7/")

    def test_library_sync_defaults_to_movies_and_allows_series_toggle(self):
        settings = {}
        self.service.ADDON.getSetting = lambda name: settings.get(name, "")

        self.assertEqual(self.service._sync_media_types(), frozenset({"movie"}))

        settings.update(sync_movies="false", sync_series="true")
        self.assertEqual(self.service._sync_media_types(), frozenset({"series"}))

        settings["sync_movies"] = "true"
        self.assertEqual(
            self.service._sync_media_types(),
            frozenset({"movie", "series"}),
        )

        settings.update(sync_movies="false", sync_series="false")
        with self.assertRaisesRegex(ValueError, "Enable movie sync"):
            self.service._sync_media_types()

    def test_client_initialization_failure_does_not_break_later_sync_requests(self):
        with tempfile.TemporaryDirectory() as library_dir:
            monitor = _Monitor()
            valid_client = object()
            self.service._client = mock.Mock(
                side_effect=[ValueError("invalid portal URL"), valid_client]
            )
            self.service._perform_sync = mock.Mock()

            self.service._run_sync_request(library_dir, {}, monitor)

            self.service._perform_sync.assert_not_called()
            self.assertIn(
                "sync request failed",
                self.error_dialogs[0][1].casefold(),
            )
            self.assertIn("invalid portal URL", self.error_dialogs[0][1])
            self.service._run_sync_request(library_dir, {}, monitor)
            self.service._perform_sync.assert_called_once_with(
                library_dir,
                {},
                valid_client,
                monitor,
                self.progress_dialogs[1],
                frozenset({"movie"}),
            )
            self.assertTrue(all(dialog.closed for dialog in self.progress_dialogs))

    def test_running_sync_is_not_started_or_cleared_by_another_request(self):
        with tempfile.TemporaryDirectory() as library_dir:
            self.service._write_state(
                library_dir,
                {"sync_status": {"status": "running", "started_at": 1000}},
            )
            self.service._client = mock.Mock()
            self.service._perform_sync = mock.Mock()

            self.service._run_sync_request(library_dir, {}, _Monitor())

            self.service._client.assert_not_called()
            self.service._perform_sync.assert_not_called()
            self.assertEqual(
                self.service._read_state(library_dir)["sync_status"]["status"],
                "running",
            )
            self.assertIn("already running", self.notifications[-1][1].casefold())

    def test_manual_sync_is_not_blocked_by_recent_success(self):
        with tempfile.TemporaryDirectory() as library_dir:
            self.service._write_state(
                library_dir,
                {"last_success": time.time()},
            )
            self.service._client = mock.Mock()
            self.service._perform_sync = mock.Mock(return_value=True)

            self.service._run_sync_request(library_dir, {}, _Monitor())

            self.service._client.assert_called_once()
            self.service._perform_sync.assert_called_once()
            self.assertEqual(
                self.service._read_state(library_dir)["auto_sync"]["status"],
                "complete",
            )

    def test_request_queued_during_a_sync_runs_after_current_sync(self):
        with tempfile.TemporaryDirectory() as library_dir:
            request_path = Path(library_dir, "sync.request")
            self.service._client = mock.Mock(return_value=object())

            def queue_another_request(*args):
                request_path.touch()

            self.service._perform_sync = mock.Mock(side_effect=queue_another_request)

            self.service._run_sync_request(library_dir, {}, _Monitor())

            self.assertTrue(request_path.exists())

    def test_sync_progress_updates_background_dialog(self):
        with tempfile.TemporaryDirectory() as library_dir:
            self.service._write_state(
                library_dir,
                {
                    "registration_restart_pending": True,
                    "sync_status": {"status": "running"},
                },
            )

            def finish_crawl(client, path, cancelled, progress, media_types):
                self.assertEqual(media_types, frozenset({"movie"}))
                progress(5, 12, "English Movies")
                return SyncResult(True, 10, 2, 5, (), 0)

            with mock.patch.object(self.service, "run_sync", side_effect=finish_crawl):
                dialog = _ProgressDialog()
                self.service._perform_sync(
                    library_dir,
                    {"movies": "Movies", "tv": "TV Shows"},
                    object(),
                    _Monitor(),
                    dialog,
                )

            self.assertTrue(dialog.updates)
            self.assertEqual(dialog.updates[-1][0], 0)
            self.assertIn("5 pages visited", dialog.updates[-1][2])

    def test_complete_sync_queues_library_scan_for_discovered_items(self):
        with tempfile.TemporaryDirectory() as library_dir:
            paths = {
                "movies": str(Path(library_dir) / "Movies"),
                "tv": str(Path(library_dir) / "TV Shows"),
            }
            self.service._ensure_sources = mock.Mock(return_value=False)
            sync_result = SyncResult(
                True,
                18,
                2,
                7,
                (),
                0,
            )
            with mock.patch.object(self.service, "run_sync", return_value=sync_result):
                self.service._perform_sync(
                    library_dir,
                    paths,
                    object(),
                    _Monitor(),
                    _ProgressDialog(),
                )

            self.assertEqual(
                self.builtins,
                [f'UpdateLibrary(video,"{paths["movies"]}")'],
            )
            state = self.service._read_state(library_dir)
            self.assertEqual(
                state["library_refresh"],
                {"queue": [paths["tv"]], "clean": False, "active": paths["movies"]},
            )
            self.assertIn(
                "sync complete",
                self.notifications[-1][1].casefold(),
            )

    def test_fresh_profile_is_eligible_for_one_initial_auto_sync(self):
        with tempfile.TemporaryDirectory() as library_dir:
            state = self.service._initialize_auto_sync(library_dir)

            self.assertEqual(state["auto_sync"]["status"], "pending")
            self.assertTrue(self.service._auto_sync_due(state))
            self.assertEqual(self.service.MAX_INITIAL_SYNC_ATTEMPTS, 3)

    def test_existing_profile_does_not_auto_sync_after_addon_update(self):
        with tempfile.TemporaryDirectory() as library_dir:
            self.service._write_state(library_dir, {"last_success": 1234})

            state = self.service._initialize_auto_sync(library_dir)

            self.assertEqual(state["auto_sync"]["status"], "disabled")
            self.assertFalse(self.service._auto_sync_due(state))

    def test_automatic_sync_retries_three_times_and_then_exhausts(self):
        with tempfile.TemporaryDirectory() as library_dir:
            self.service._initialize_auto_sync(library_dir)
            self.service._client = mock.Mock(return_value=object())
            self.service._perform_sync = mock.Mock(return_value=False)

            for attempt in range(1, 4):
                self.service._run_sync_request(
                    library_dir, {}, _Monitor(), automatic=True
                )
                state = self.service._read_state(library_dir)
                self.assertEqual(state["auto_sync"]["attempts"], attempt)
                if attempt < 3:
                    self.assertEqual(state["auto_sync"]["status"], "retry_pending")
                    self.assertFalse(
                        any(
                            "could not complete after 3 attempts" in item[1]
                            for item in self.notifications
                        )
                    )

            self.assertEqual(state["auto_sync"]["status"], "exhausted")
            self.assertTrue(
                any(
                    "could not complete after 3 attempts" in item[1]
                    for item in self.notifications
                )
            )
            self.assertEqual(self.service._perform_sync.call_count, 3)

    def test_incomplete_sync_shows_detailed_errors_in_dialog(self):
        result = SyncResult(
            False,
            0,
            0,
            12,
            ("http://172.16.50.7/movies/: connection refused",),
            0,
        )
        with tempfile.TemporaryDirectory() as library_dir:
            self.service._ensure_sources = mock.Mock(return_value=False)
            self.service._begin_library_refresh = mock.Mock()
            with mock.patch.object(self.service, "run_sync", return_value=result):
                self.service._perform_sync(
                    library_dir,
                    {"movies": "Movies", "tv": "TV Shows"},
                    object(),
                    _Monitor(),
                    _ProgressDialog(),
                )

        self.assertIn("connection refused", self.error_dialogs[-1][1])
        self.assertIn("nothing was pruned", self.error_dialogs[-1][1])

    def test_sync_exception_shows_filesystem_diagnostics_in_dialog(self):
        error = OSError(
            "[WinError 3] atomic replace diagnostics: "
            "destination_length=277, temporary_length=243, parent_exists=True"
        )
        with tempfile.TemporaryDirectory() as library_dir:
            self.service._ensure_sources = mock.Mock(return_value=False)
            with mock.patch.object(
                self.service,
                "run_sync",
                side_effect=error,
            ):
                self.service._perform_sync(
                    library_dir,
                    {"movies": "Movies", "tv": "TV Shows"},
                    object(),
                    _Monitor(),
                    _ProgressDialog(),
                )

        details = self.error_dialogs[-1][1]
        self.assertIn("destination_length=277", details)
        self.assertIn("temporary_length=243", details)
        self.assertIn("parent_exists=True", details)

    def test_successful_manual_sync_satisfies_fresh_install_auto_sync(self):
        with tempfile.TemporaryDirectory() as library_dir:
            self.service._initialize_auto_sync(library_dir)
            self.service._client = mock.Mock(return_value=object())
            self.service._perform_sync = mock.Mock(return_value=True)

            self.service._run_sync_request(library_dir, {}, _Monitor())

            state = self.service._read_state(library_dir)
            self.assertEqual(state["auto_sync"]["status"], "complete")
            self.assertFalse(self.service._auto_sync_due(state))

    def test_non_object_state_is_quarantined_explicitly(self):
        with tempfile.TemporaryDirectory() as library_dir:
            path = Path(library_dir, "state.json")
            path.write_text("[]", encoding="utf-8")
            self.assertEqual(self.service._read_state(library_dir), {})
            self.assertFalse(path.exists())
            self.assertTrue(Path(str(path) + ".corrupt").is_file())

    def test_ensure_sources_registers_paths_in_sources_xml(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            profile_dir = Path(temp_dir) / "profile"
            profile_dir.mkdir(parents=True)
            sources_path = profile_dir / "sources.xml"

            def translate_path(value):
                if value == "special://profile/sources.xml":
                    return str(sources_path)
                return str(profile_dir)

            self.service.xbmcvfs.translatePath = translate_path
            paths = self.service.library_paths(str(profile_dir))
            library_dir = paths["state"]
            state = {}

            changed = self.service._ensure_sources(library_dir, paths, state)

            self.assertTrue(changed)
            self.assertTrue(Path(paths["movies"]).is_dir())
            self.assertTrue(Path(paths["tv"]).is_dir())
            self.assertTrue(sources_path.is_file())
            self.assertFalse(
                self.service._ensure_sources(library_dir, paths, state)
            )

    def test_registered_source_refresh_resumes_after_restart_without_confirmation(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            profile_dir = Path(temp_dir) / "profile"
            profile_dir.mkdir(parents=True)
            paths = self.service.library_paths(str(profile_dir))
            sources_path = profile_dir / "sources.xml"
            for key in ("movies", "tv"):
                Path(paths[key]).mkdir(parents=True)
            self.service.register_sources(
                str(sources_path),
                paths["movies"],
                paths["tv"],
            )
            self.service.xbmcvfs.translatePath = lambda value: (
                str(sources_path) if value == "special://profile/sources.xml"
                else str(profile_dir)
            )
            library_dir = paths["state"]
            state = {"registration_restart_pending": True, "last_success": 1}
            self.service._write_state(library_dir, state)

            self.assertFalse(self.service._ensure_sources(library_dir, paths, state))
            self.service._resume_pending_refresh(
                library_dir,
                paths,
                resume_existing=True,
            )

            self.assertEqual(
                self.builtins,
                [f'UpdateLibrary(video,"{paths["movies"]}")'],
            )
            self.assertEqual(
                self.service._read_state(library_dir)["library_refresh"]["active"],
                paths["movies"],
            )


if __name__ == "__main__":
    unittest.main()
