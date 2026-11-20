import importlib.util
import json
import sys
import tempfile
import threading
import types
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import ClassVar
from unittest import mock
from urllib.parse import urlencode

ADDON_DIR = Path(__file__).resolve().parents[1] / "plugin.bdix.dhakaflix"
sys.path.insert(0, str(ADDON_DIR))


class _Handler(BaseHTTPRequestHandler):
    requests = 0

    def do_POST(self):
        type(self).requests += 1
        length = int(self.headers.get("Content-Length", "0"))
        json.loads(self.rfile.read(length).decode("utf-8"))
        body = json.dumps(
            {
                "search": [
                    {
                        "href": "/DHAKA-FLIX-7/Movies/Film%20(2026).mkv",
                        "size": 1073741824,
                        "time": 1767225600000,
                    }
                ]
            }
        ).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        type(self).requests += 1
        body = (
            b'<div id="fallback"><table>'
            b'<tr><td class="fb-i"><img src="folder.png"></td>'
            b'<td class="fb-n"><a href="folder/">Folder</a></td>'
            b'<td class="fb-d">2026-01-01 10:00</td><td class="fb-s">-</td></tr>'
            b'<tr><td class="fb-i"><img src="file.png"></td>'
            b'<td class="fb-n"><a href="film.mkv">Film (2026).mkv</a></td>'
            b'<td class="fb-d">2026-01-02 12:30</td><td class="fb-s">1.4 GB</td></tr>'
            b'<tr><td class="fb-i"><img src="file.png"></td>'
            b'<td class="fb-n"><a href="Film%20(2026).en.srt">'
            b"Film (2026).en.srt</a></td>"
            b'<td class="fb-d"></td><td class="fb-s"></td></tr>'
            b'<tr><td class="fb-i"><img src="file.png"></td>'
            b'<td class="fb-n"><a href="cover.jpg">cover.jpg</a></td>'
            b'<td class="fb-d"></td><td class="fb-s"></td></tr>'
            b"</table></div>"
        )
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, fmt, *args):
        pass


class _VideoInfoTag:
    def __init__(self):
        self.title = ""
        self.mediatype = ""
        self.studios = []
        self.year = None
        self.season = None
        self.episode = None
        self.dateadded = ""

    def setTitle(self, title):
        self.title = title

    def setMediaType(self, mediatype):
        self.mediatype = mediatype

    def setStudios(self, studios):
        self.studios = list(studios)

    def setYear(self, year):
        self.year = year

    def setSeason(self, season):
        self.season = season

    def setEpisode(self, episode):
        self.episode = episode

    def setDateAdded(self, dateadded):
        self.dateadded = dateadded


class _ListItem:
    def __init__(self, label="", label2="", path="", offscreen=False):
        self.label = label
        self.label2 = label2
        self.path = path
        self.offscreen = offscreen
        self.art = {}
        self.properties = {}
        self.context_menu = []
        self.subtitles = []
        self.video_tag = _VideoInfoTag()

    def getVideoInfoTag(self):
        return self.video_tag

    def setArt(self, art):
        self.art = art

    def setProperty(self, key, value):
        self.properties[key] = value

    def setPath(self, path):
        self.path = path

    def addContextMenuItems(self, items):
        self.context_menu.extend(items)

    def setSubtitles(self, subtitles):
        self.subtitles = subtitles


class _Keyboard:
    confirmed = False
    text = ""

    def __init__(self, value, heading):
        self.value = value
        self.heading = heading

    def doModal(self):
        pass

    def isConfirmed(self):
        return self.confirmed

    def getText(self):
        return self.text or self.value


class _Addon:
    values: ClassVar[dict] = {}

    def __init__(self, id):
        self.id = id

    def getAddonInfo(self, name):
        return {
            "name": "DhakaFlix",
            "version": "0.5.0",
            "profile": self.values["profile"],
        }[name]

    def getSetting(self, name):
        return self.values.get(name, "")

    def openSettings(self):
        pass


class KodiEntrypointTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = HTTPServer(("127.0.0.1", 0), _Handler)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.origin = f"http://127.0.0.1:{cls.server.server_port}"

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.thread.join(timeout=2)
        cls.server.server_close()

    def setUp(self):
        self.profile = tempfile.TemporaryDirectory()
        _Addon.values = {
            "profile": self.profile.name,
            "excluded_keywords": "",
            "cache_ttl": "0",
            "request_timeout": "3",
            "server_7_url": "http://172.16.50.7/",
            "server_10_url": "http://172.16.50.10/",
            "server_12_url": "http://172.16.50.12/",
            "server_14_url": "http://172.16.50.14/",
        }
        self.items = []
        self.resolved = []
        self.end_calls = []
        self.sort_calls = []
        self.notifications = []
        self.error_dialogs = []
        self.builtins = []
        self.logs = []
        xbmc = types.ModuleType("xbmc")
        xbmc.Keyboard = _Keyboard
        xbmc.LOGINFO = 1
        xbmc.LOGWARNING = 2
        xbmc.LOGERROR = 3
        xbmc.log = lambda message, level=1: self.logs.append((message, level))
        xbmc.executebuiltin = lambda command: self.builtins.append(command)
        xbmcaddon = types.ModuleType("xbmcaddon")
        xbmcaddon.Addon = _Addon
        xbmcgui = types.ModuleType("xbmcgui")
        xbmcgui.ListItem = _ListItem
        xbmcgui.Keyboard = _Keyboard
        xbmcgui.Dialog = lambda: types.SimpleNamespace(
            notification=lambda *args: self.notifications.append(args),
            ok=lambda *args: self.error_dialogs.append(args),
        )
        xbmcplugin = types.ModuleType("xbmcplugin")
        xbmcplugin.SORT_METHOD_UNSORTED = 40
        xbmcplugin.SORT_METHOD_LABEL_IGNORE_FOLDERS = 2
        xbmcplugin.SORT_METHOD_DATEADDED = 21
        xbmcplugin.setContent = lambda *args: None
        xbmcplugin.addSortMethod = lambda handle, method: self.sort_calls.append(
            (handle, method)
        )
        xbmcplugin.addDirectoryItems = (
            lambda handle, items, total=0: self.items.extend(
                {
                    "handle": handle,
                    "url": url,
                    "listitem": item,
                    "isFolder": is_folder,
                }
                for url, item, is_folder in items
            )
            or True
        )
        xbmcplugin.endOfDirectory = (
            lambda handle, **kwargs: self.end_calls.append({"handle": handle, **kwargs})
        )
        xbmcplugin.setResolvedUrl = lambda *args: self.resolved.append(args)
        xbmcvfs = types.ModuleType("xbmcvfs")
        xbmcvfs.translatePath = lambda value: value
        self.modules = {
            "xbmc": xbmc,
            "xbmcaddon": xbmcaddon,
            "xbmcgui": xbmcgui,
            "xbmcplugin": xbmcplugin,
            "xbmcvfs": xbmcvfs,
        }
        self.module_patcher = mock.patch.dict(sys.modules, self.modules)
        self.module_patcher.start()
        self.addCleanup(self.module_patcher.stop)

    def tearDown(self):
        self.profile.cleanup()

    def _load_module(self):
        source_path = ADDON_DIR / "default.py"
        spec = importlib.util.spec_from_file_location(
            "dhakaflix_test_entry", source_path
        )
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        self.addCleanup(module.reset_runtime_state)
        return module

    def _invoke(self, query, node_urls=None, handle="1", module=None):
        if node_urls is not None:
            for node_id in ("7", "10", "12", "14"):
                _Addon.values[f"server_{node_id}_url"] = node_urls.get(node_id, "")
        argv = ["plugin://plugin.bdix.dhakaflix/", str(handle), query]
        if module is None:
            module = self._load_module()
        with mock.patch.object(sys, "argv", argv):
            module.main()
        return module

    def test_home_displays_navigation_search_and_cache_actions(self):
        self._invoke("")
        self.assertEqual(
            [item["listitem"].label for item in self.items],
            [
                "Browse DhakaFlix portal",
                "Movie categories",
                "TV & series categories",
                "Search movies",
                "Clear listing cache",
                "Add-on settings",
            ],
        )
        self.assertTrue(self.items[1]["url"].endswith("type=movie"))
        self.assertTrue(self.items[3]["url"].endswith("type=movie"))
        self.assertTrue(self.items[4]["url"].endswith("action=clear_cache"))
        self.assertTrue(self.end_calls[-1]["succeeded"])

    def test_browse_route_lists_folders_and_playable_video(self):
        self._invoke(
            "?" + urlencode({"action": "browse", "url": self.origin + "/"}),
            node_urls={key: self.origin + "/" for key in ("7", "10", "12", "14")},
        )
        self.assertEqual(len(self.items), 2)
        self.assertEqual(
            [item["listitem"].label for item in self.items],
            ["Folder", "Film (2026).mkv"],
        )
        self.assertTrue(all(item["listitem"].offscreen for item in self.items))
        self.assertTrue(self.items[0]["isFolder"])
        self.assertFalse(self.items[1]["isFolder"])
        self.assertEqual(self.items[1]["listitem"].label2, "1.4 GB")
        self.assertEqual(self.items[1]["listitem"].video_tag.year, 2026)
        self.assertEqual(self.items[1]["listitem"].video_tag.mediatype, "movie")
        self.assertEqual(
            self.items[1]["listitem"].video_tag.dateadded,
            "2026-01-02 12:30:00",
        )
        self.assertEqual(
            self.items[1]["listitem"].subtitles,
            [self.origin + "/Film%20(2026).en.srt"],
        )
        self.assertEqual(
            self.items[1]["listitem"].art["thumb"],
            self.origin + "/cover.jpg",
        )
        self.assertIn("action=browse", self.items[0]["url"])
        self.assertIn("action=play", self.items[1]["url"])
        self.assertTrue(self.items[0]["listitem"].context_menu)
        self.assertTrue(self.sort_calls)
        self.assertTrue(self.end_calls[-1]["succeeded"])

    def test_movie_catalog_routes_to_multiple_nodes(self):
        self._invoke("?action=catalog&type=movie")
        routes = [item["url"] for item in self.items]
        self.assertGreaterEqual(len(routes), 10)
        self.assertTrue(any("172.16.50.7" in route for route in routes))
        self.assertTrue(any("172.16.50.14" in route for route in routes))
        self.assertTrue(all(item["isFolder"] for item in self.items))

    def test_movie_search_uses_fixed_node_urls_and_lists_direct_playback(self):
        _Keyboard.confirmed = True
        _Keyboard.text = "Film"
        self.addCleanup(setattr, _Keyboard, "confirmed", False)
        self.addCleanup(setattr, _Keyboard, "text", "")
        self._invoke(
            "?action=search&type=movie",
            node_urls={
                "7": self.origin + "/",
                "10": "",
                "12": "",
                "14": "",
            },
        )
        self.assertEqual(len(self.items), 1)
        self.assertEqual(self.items[0]["listitem"].label, "Film (2026).mkv")
        self.assertEqual(self.items[0]["listitem"].label2, "1.0 GB")
        self.assertFalse(self.items[0]["isFolder"])
        self.assertIn("action=play", self.items[0]["url"])
        self.assertEqual(
            self.items[0]["listitem"].video_tag.studios,
            ["DhakaFlix node 7"],
        )
        self.assertEqual(
            self.items[0]["listitem"].context_menu[0][0],
            "Open parent folder",
        )
        self.assertTrue(self.end_calls[-1]["cacheToDisc"])

    def test_search_cancel_and_blank_query_do_not_open_empty_directory(self):
        _Keyboard.confirmed = False
        self._invoke("?action=search&type=movie")
        self.assertFalse(self.end_calls[-1]["succeeded"])

        _Keyboard.confirmed = True
        _Keyboard.text = "   "
        self.addCleanup(setattr, _Keyboard, "confirmed", False)
        self.addCleanup(setattr, _Keyboard, "text", "")
        self._invoke("?action=search&type=movie")
        self.assertFalse(self.end_calls[-1]["succeeded"])

    def test_play_route_resolves_direct_media_url_with_cached_subtitles_and_art(self):
        _Addon.values["cache_ttl"] = "3600"
        module = self._load_module()
        self._invoke(
            "?" + urlencode({"action": "browse", "url": self.origin + "/"}),
            node_urls={key: self.origin + "/" for key in ("7", "10", "12", "14")},
            handle="1",
            module=module,
        )
        target = self.origin + "/film.mkv"
        self._invoke(
            "?" + urlencode({"action": "play", "url": target}),
            handle="2",
            module=module,
        )
        self.assertEqual(len(self.resolved), 1)
        handle, succeeded, item = self.resolved[0]
        self.assertEqual(handle, 2)
        self.assertTrue(succeeded)
        self.assertEqual(item.path, target)
        self.assertEqual(item.video_tag.title, "Film (2026)")
        self.assertEqual(item.video_tag.year, 2026)
        self.assertEqual(item.video_tag.mediatype, "movie")
        self.assertEqual(item.video_tag.dateadded, "2026-01-02 12:30:00")
        self.assertEqual(item.art["thumb"], self.origin + "/cover.jpg")
        self.assertEqual(item.subtitles, [self.origin + "/Film%20(2026).en.srt"])

    def test_refresh_runplugin_invalidates_cache_and_refreshes_container(self):
        _Addon.values["cache_ttl"] = "3600"
        _Handler.requests = 0
        module = self._load_module()
        target = self.origin + "/folder/"
        nodes = {key: self.origin + "/" for key in ("7", "10", "12", "14")}
        self._invoke(
            "?" + urlencode({"action": "browse", "url": target}),
            node_urls=nodes,
            handle="1",
            module=module,
        )
        self.assertEqual(_Handler.requests, 1)

        self._invoke(
            "?" + urlencode({"action": "refresh", "url": target}),
            handle="-1",
            module=module,
        )
        self.assertEqual(self.builtins, ["Container.Refresh"])
        self.assertEqual(len(self.end_calls), 1)

        self._invoke(
            "?" + urlencode({"action": "browse", "url": target}),
            handle="2",
            module=module,
        )
        self.assertEqual(_Handler.requests, 2)

    def test_clear_cache_route_empties_cache_and_notifies(self):
        _Addon.values["cache_ttl"] = "3600"
        _Handler.requests = 0
        module = self._load_module()
        target = self.origin + "/cached/"
        nodes = {key: self.origin + "/" for key in ("7", "10", "12", "14")}
        self._invoke(
            "?" + urlencode({"action": "browse", "url": target}),
            node_urls=nodes,
            handle="1",
            module=module,
        )
        self._invoke("?action=clear_cache", handle="-1", module=module)
        self.assertTrue(
            any("Listing cache cleared" in note[1] for note in self.notifications)
        )
        self._invoke(
            "?" + urlencode({"action": "browse", "url": target}),
            handle="2",
            module=module,
        )
        self.assertEqual(_Handler.requests, 2)

    def test_reuselanguageinvoker_handles_repeated_main_calls_with_new_handles(self):
        module = self._load_module()
        self._invoke("", handle="10", module=module)
        self.assertEqual(self.items[0]["handle"], 10)
        self.assertEqual(self.end_calls[-1]["handle"], 10)

        self.items.clear()
        self._invoke("?action=catalog&type=movie", handle="11", module=module)
        self.assertEqual(self.items[0]["handle"], 11)
        self.assertEqual(self.end_calls[-1]["handle"], 11)

    def test_portal_url_stays_fixed_while_nodes_are_configurable(self):
        module = self._invoke("")
        origin_url, _, _, _, node_urls = module._settings(module.xbmcaddon.Addon("id"))

        self.assertEqual(origin_url, "http://172.16.50.7/")
        self.assertEqual(
            node_urls,
            {
                "7": "http://172.16.50.7/",
                "10": "http://172.16.50.10/",
                "12": "http://172.16.50.12/",
                "14": "http://172.16.50.14/",
            },
        )

    def test_node_urls_can_be_overridden_without_a_portal_url_setting(self):
        module = self._invoke(
            "",
            node_urls={
                "7": self.origin + "/",
                "10": "",
                "12": self.origin + "/",
                "14": "",
            },
        )
        origin_url, _, _, _, node_urls = module._settings(module.xbmcaddon.Addon("id"))

        self.assertEqual(origin_url, "http://172.16.50.7/")
        self.assertEqual(
            node_urls,
            {
                "7": self.origin + "/",
                "10": "",
                "12": self.origin + "/",
                "14": "",
            },
        )

    def test_partial_search_failure_notifies_without_modal_error_dialog(self):
        _Keyboard.confirmed = True
        _Keyboard.text = "Film"
        self.addCleanup(setattr, _Keyboard, "confirmed", False)
        self.addCleanup(setattr, _Keyboard, "text", "")
        self._invoke(
            "?action=search&type=movie",
            node_urls={
                "7": self.origin + "/",
                "10": "",
                "12": "",
                "14": "http://127.0.0.1:1/",
            },
        )
        self.assertEqual(len(self.items), 1)
        self.assertEqual(self.error_dialogs, [])
        self.assertTrue(
            any(
                "Some search sources could not be reached" in note[1]
                for note in self.notifications
            )
        )
        self.assertTrue(any("Node 14:" in msg for msg, _ in self.logs))


if __name__ == "__main__":
    unittest.main()
