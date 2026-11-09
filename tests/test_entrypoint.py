import json
import importlib.util
import sys
import tempfile
import threading
import types
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from unittest import mock
from urllib.parse import urlencode

ADDON_DIR = Path(__file__).resolve().parents[1] / "plugin.bdix.dhakaflix"
sys.path.insert(0, str(ADDON_DIR))


class _Handler(BaseHTTPRequestHandler):
    def do_POST(self):
        length = int(self.headers.get("Content-Length", "0"))
        json.loads(self.rfile.read(length).decode("utf-8"))
        body = json.dumps(
            {"search": [{"href": "/DHAKA-FLIX-7/Film.mkv", "size": "1 GB"}]}
        ).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        body = (
            b'<a href="folder/">Folder</a>'
            b'<a href="film.mkv">Film.mkv</a>'
            b'<a href="Film.en.srt">Film.en.srt</a>'
            b'<a href="cover.jpg">cover.jpg</a>'
        )
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, fmt, *args):
        pass


class _ListItem:
    def __init__(self, label="", path=""):
        self.label = label
        self.path = path
        self.art = {}
        self.info = {}
        self.properties = {}
        self.context_menu = []
        self.subtitles = []

    def setArt(self, art):
        self.art = art

    def setInfo(self, kind, info):
        self.info[kind] = info

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
    values = {}

    def __init__(self, id):
        self.id = id

    def getAddonInfo(self, name):
        return {
            "name": "DhakaFlix",
            "version": "0.1.0",
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
        cls.origin = "http://127.0.0.1:{}".format(cls.server.server_port)

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.thread.join(timeout=2)
        cls.server.server_close()

    def setUp(self):
        self.profile = tempfile.TemporaryDirectory()
        _Addon.values = {
            "profile": self.profile.name,
            "origin_url": self.origin + "/",
            "excluded_keywords": "",
            "cache_ttl": "0",
            "request_timeout": "3",
            "server_7_url": "http://172.16.50.7/",
            "server_9_url": "http://172.16.50.9/",
            "server_12_url": "http://172.16.50.12/",
            "server_14_url": "http://172.16.50.14/",
        }
        self.items = []
        self.resolved = []
        self.end_calls = []
        self.logs = []
        xbmc = types.ModuleType("xbmc")
        xbmc.LOGINFO = 1
        xbmc.LOGWARNING = 2
        xbmc.LOGERROR = 3
        xbmc.log = lambda *args: self.logs.append(args)
        xbmc.Keyboard = _Keyboard
        xbmcaddon = types.ModuleType("xbmcaddon")
        xbmcaddon.Addon = _Addon
        xbmcgui = types.ModuleType("xbmcgui")
        xbmcgui.ListItem = _ListItem
        xbmcgui.Keyboard = _Keyboard
        xbmcgui.Dialog = lambda: types.SimpleNamespace(
            notification=lambda *args: None,
            ok=lambda *args: None,
        )
        xbmcplugin = types.ModuleType("xbmcplugin")
        xbmcplugin.setContent = lambda *args: None
        xbmcplugin.addDirectoryItem = lambda **kwargs: self.items.append(kwargs) or True
        xbmcplugin.endOfDirectory = lambda *args, **kwargs: self.end_calls.append(kwargs)
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

    def _invoke(self, query):
        argv = ["plugin://plugin.bdix.dhakaflix/", "1", query]
        with mock.patch.object(sys, "argv", argv):
            source_path = ADDON_DIR / "default.py"
            spec = importlib.util.spec_from_file_location("dhakaflix_test_entry", source_path)
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            module.main()

    def test_home_displays_multi_node_catalog_and_search(self):
        self._invoke("")
        self.assertFalse(self.logs, self.logs)
        self.assertEqual(
            [item["listitem"].label for item in self.items],
            [
                "Browse configured portal",
                "Movie categories",
                "TV & series categories",
                "Search movies",
                "Search TV & series",
                "Add-on settings",
            ],
        )
        self.assertTrue(self.items[1]["url"].endswith("type=movie"))
        self.assertTrue(self.items[3]["url"].endswith("type=movie"))
        self.assertTrue(self.end_calls[-1]["succeeded"])

    def test_browse_route_lists_folders_and_playable_video(self):
        self._invoke("?" + urlencode({"action": "browse", "url": self.origin + "/"}))
        self.assertFalse(self.logs, self.logs)
        self.assertEqual(len(self.items), 2)
        self.assertEqual(
            [item["listitem"].label for item in self.items],
            ["Folder", "Film.mkv"],
        )
        self.assertTrue(self.items[0]["isFolder"])
        self.assertFalse(self.items[1]["isFolder"])
        self.assertEqual(self.items[1]["listitem"].subtitles, [
            self.origin + "/Film.en.srt",
        ])
        self.assertEqual(
            self.items[1]["listitem"].art["thumb"],
            self.origin + "/cover.jpg",
        )
        self.assertIn("action=browse", self.items[0]["url"])
        self.assertIn("action=play", self.items[1]["url"])
        self.assertTrue(self.items[0]["listitem"].context_menu)
        self.assertTrue(self.end_calls[-1]["succeeded"])

    def test_movie_catalog_routes_to_multiple_nodes(self):
        self._invoke("?action=catalog&type=movie")
        self.assertFalse(self.logs, self.logs)
        routes = [item["url"] for item in self.items]
        self.assertGreaterEqual(len(routes), 10)
        self.assertTrue(any("172.16.50.7" in route for route in routes))
        self.assertTrue(any("172.16.50.14" in route for route in routes))
        self.assertTrue(all(item["isFolder"] for item in self.items))

    def test_movie_search_uses_configured_nodes_and_lists_direct_playback(self):
        _Keyboard.confirmed = True
        _Keyboard.text = "Film"
        self.addCleanup(setattr, _Keyboard, "confirmed", False)
        self.addCleanup(setattr, _Keyboard, "text", "")
        _Addon.values.update({
            "server_7_url": self.origin + "/",
            "server_9_url": "",
            "server_12_url": "",
            "server_14_url": "",
        })
        self._invoke("?action=search&type=movie")
        self.assertFalse(self.logs, self.logs)
        self.assertEqual(len(self.items), 1)
        self.assertEqual(self.items[0]["listitem"].label, "Film.mkv")
        self.assertFalse(self.items[0]["isFolder"])
        self.assertIn("action=play", self.items[0]["url"])
        self.assertEqual(
            self.items[0]["listitem"].info["video"]["studio"],
            "DhakaFlix node 7",
        )

    def test_play_route_resolves_direct_media_url(self):
        target = self.origin + "/film%20(2026).mkv"
        self._invoke("?" + urlencode({"action": "play", "url": target}))
        self.assertFalse(self.logs, self.logs)
        self.assertEqual(len(self.resolved), 1)
        handle, succeeded, item = self.resolved[0]
        self.assertEqual(handle, 1)
        self.assertTrue(succeeded)
        self.assertEqual(item.path, target)
        self.assertEqual(item.info["video"]["title"], "film (2026).mkv")


if __name__ == "__main__":
    unittest.main()
