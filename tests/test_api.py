"""HTTP API tests: start a real hindsightd on a free port and talk to it."""
from __future__ import annotations

import json
import shutil
import sys
import tempfile
import threading
import unittest
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from hindsightd.server import Hindsight, serve  # noqa: E402


class ApiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = Path(tempfile.mkdtemp(prefix="hindsight-api-"))
        share = cls.tmp / "share"
        for app_id, name in [("notes", "Notes"), ("settings", "Settings")]:
            d = share / "apps" / app_id
            d.mkdir(parents=True)
            (d / "manifest.json").write_text(json.dumps({"id": app_id, "name": name}))
            (d / "index.html").write_text(f"<h1>{name}</h1>")
        (share / "shell").mkdir()
        (share / "shell" / "index.html").write_text("<title>Hindsight</title>")
        (share / "sdk").mkdir()
        (share / "sdk" / "hindsight.js").write_text("window.Hindsight = {};")
        cls.hs = Hindsight(cls.tmp / "data", [share / "apps"], share / "shell",
                           share / "sdk", backend="copy", log=lambda *_: None)
        cls.httpd = serve(cls.hs, "127.0.0.1", 0)
        cls.base = f"http://127.0.0.1:{cls.httpd.server_address[1]}"
        threading.Thread(target=cls.httpd.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()
        cls.httpd.server_close()
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def call(self, method, path, body=None):
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(self.base + path, data=data, method=method,
                                     headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=5) as resp:
                return resp.status, json.loads(resp.read() or b"null")
        except urllib.error.HTTPError as err:
            return err.code, json.loads(err.read() or b"null")

    def kv(self, key):
        return "/api/apps/notes/kv/" + urllib.parse.quote(key, safe="")

    def test_static_files(self):
        with urllib.request.urlopen(self.base + "/", timeout=5) as resp:
            self.assertIn(b"Hindsight", resp.read())
        with urllib.request.urlopen(self.base + "/apps/notes/", timeout=5) as resp:
            self.assertIn(b"Notes", resp.read())
        status, _ = self.call("GET", "/apps/notes/../../share/apps/notes/manifest.json")
        self.assertIn(status, (200, 404))  # normalised, never escapes the app folder
        status, _ = self.call("GET", "/apps/notes/%2e%2e/%2e%2e/%2e%2e/etc/passwd")
        self.assertEqual(status, 404)

    def test_full_rewind_flow_over_http(self):
        status, apps = self.call("GET", "/api/apps")
        self.assertEqual(status, 200)
        self.assertEqual({a["id"] for a in apps["apps"]}, {"notes", "settings"})

        self.call("PUT", self.kv("note:groceries"), {"value": {"title": "Groceries"}})
        self.call("PUT", self.kv("note/with slash"), {"value": 1})
        status, rec = self.call("GET", self.kv("note/with slash"))
        self.assertEqual((status, rec["value"]), (200, 1))

        # Deleting takes a labeled snapshot first.
        label = urllib.parse.quote("Before deleting “Groceries”")
        status, _ = self.call("DELETE", self.kv("note:groceries") + f"?label={label}")
        self.assertEqual(status, 200)
        status, snaps = self.call("GET", "/api/apps/notes/snapshots")
        before_delete = snaps["snapshots"][-1]
        self.assertEqual(before_delete["label"], "Before deleting “Groceries”")

        # Preview the past without restoring.
        status, past = self.call("GET", f"/api/apps/notes/kv?at={before_delete['id']}")
        self.assertIn("note:groceries", past["records"])
        status, now = self.call("GET", "/api/apps/notes/kv")
        self.assertNotIn("note:groceries", now["records"])

        # Rewind, then undo.
        status, result = self.call("POST", "/api/apps/notes/rewind",
                                   {"snapshot": before_delete["id"]})
        self.assertEqual(status, 200)
        status, now = self.call("GET", "/api/apps/notes/kv")
        self.assertIn("note:groceries", now["records"])
        self.call("POST", "/api/apps/notes/rewind", {"snapshot": result["undo"]})
        status, now = self.call("GET", "/api/apps/notes/kv")
        self.assertNotIn("note:groceries", now["records"])

    def test_phone_points(self):
        self.call("PUT", self.kv("p"), {"value": "before"})
        status, created = self.call("POST", "/api/points", {"label": "Test point"})
        self.assertEqual(status, 201)
        self.call("PUT", self.kv("p"), {"value": "after"})
        status, result = self.call("POST", f"/api/points/{created['point']['id']}/rewind")
        self.assertEqual(status, 200)
        status, rec = self.call("GET", self.kv("p"))
        self.assertEqual(rec["value"], "before")

    def test_errors(self):
        self.assertEqual(self.call("GET", "/api/apps/nope/kv")[0], 404)
        self.assertEqual(self.call("GET", "/api/apps/BAD!/kv")[0], 400)
        self.assertEqual(self.call("POST", "/api/apps/notes/rewind", {"snapshot": "x"})[0], 404)
        self.assertEqual(self.call("PUT", self.kv("k"), {"nope": 1})[0], 400)
        self.assertEqual(self.call("GET", "/api/apps/notes/kv?at=20990101T000000000000Z")[0], 404)

    def test_system_info(self):
        status, info = self.call("GET", "/api/system")
        self.assertEqual(status, 200)
        self.assertEqual(info["backend"]["name"], "copy")
        self.assertIn(info["interval"], info["intervalChoices"])


if __name__ == "__main__":
    unittest.main()
