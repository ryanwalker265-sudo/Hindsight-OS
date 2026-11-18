"""Tests for the Rewind engine.

Run with:  python3 -m unittest discover -s tests -v

The same tests run twice: once with plain-folder snapshots, and once on a
real Btrfs volume (only when running as root on Linux with btrfs-progs,
which is how CI and the image build run them).
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from hindsightd.backends import BtrfsBackend, CopyBackend  # noqa: E402
from hindsightd.events import EventBus  # noqa: E402
from hindsightd.rewind import RewindEngine, thin, HOUR, DAY  # noqa: E402
from hindsightd.store import AppStore, StoreError, decode_key, encode_key  # noqa: E402


class FakeClock:
    def __init__(self, t: float = 1_800_000_000.0):
        self.t = t

    def __call__(self) -> float:
        return self.t

    def advance(self, seconds: float) -> None:
        self.t += seconds


class KeyEncodingTests(unittest.TestCase):
    def test_round_trip(self):
        for key in ["a", "note:1", "Grocery List", "émoji 😀", "x" * 150, "a/b\\c"]:
            self.assertEqual(decode_key(encode_key(key)), key)

    def test_case_insensitive_safe(self):
        self.assertNotEqual(encode_key("Note").lower(), encode_key("note").lower())

    def test_limits(self):
        with self.assertRaises(StoreError):
            encode_key("")
        with self.assertRaises(StoreError):
            encode_key("x" * 151)


class RetentionTests(unittest.TestCase):
    def test_thinning(self):
        now = 10 * DAY
        items = []
        # one snapshot per minute for the last 3 days
        for m in range(0, 3 * 24 * 60):
            items.append({"id": f"s{m}", "created": now - m * 60, "trigger": "auto"})
        items.append({"id": "labeled", "created": now - 5 * DAY, "trigger": "delete"})
        items.append({"id": "ancient", "created": now - 40 * DAY, "trigger": "auto"})
        keep = thin(items, now)
        recent = [i for i in items if now - i["created"] < 2 * HOUR]
        self.assertTrue(all(i["id"] in keep for i in recent))
        self.assertIn("labeled", keep)
        self.assertNotIn("ancient", keep)
        # between 2h and 48h: at most one per hour
        mid = [i for i in items if 2 * HOUR <= now - i["created"] < 2 * DAY and i["id"] in keep]
        self.assertLessEqual(len(mid), 47)
        self.assertGreaterEqual(len(mid), 45)


class EngineTestsMixin:
    """Behaviour every backend must have. Subclasses provide make_root()."""

    backend_cls = None

    def make_root(self) -> Path:  # pragma: no cover - overridden
        raise NotImplementedError

    def setUp(self):
        self.root = self.make_root()
        self.clock = FakeClock()
        self.backend = self.make_backend()
        self.store = AppStore(self.root, self.backend)
        self.apps = ["notes", "sketch"]
        self.bus = EventBus()
        self.engine = RewindEngine(self.store, lambda: self.apps, self.bus, clock=self.clock)
        for app in self.apps:
            self.store.ensure(app)

    def make_backend(self):
        return self.backend_cls()

    # -- helpers
    def values(self, app):
        return {k: r["value"] for k, r in self.store.records(app).items()}

    # -- tests
    def test_snapshot_reused_when_nothing_changed(self):
        self.store.set("notes", "a", 1)
        first, created1 = self.engine.snapshot("notes", "auto")
        second, created2 = self.engine.snapshot("notes", "auto")
        self.assertTrue(created1)
        self.assertFalse(created2)
        self.assertEqual(first["id"], second["id"])
        self.store.set("notes", "a", 2)
        third, created3 = self.engine.snapshot("notes", "auto")
        self.assertTrue(created3)
        self.assertNotEqual(third["id"], first["id"])

    def test_quick_same_size_writes_are_still_noticed(self):
        self.store.set("notes", "n", 1)
        first, _ = self.engine.snapshot("notes", "auto")
        self.store.set("notes", "n", 2)  # same size, possibly same timestamp
        second, created = self.engine.snapshot("notes", "auto")
        self.assertTrue(created)
        self.assertNotEqual(first["id"], second["id"])

    def test_rewind_brings_back_deleted_data_and_can_be_undone(self):
        self.store.set("notes", "groceries", {"title": "Groceries", "body": "milk"})
        self.store.set("notes", "ideas", {"title": "Ideas", "body": "an OS"})
        snap, _ = self.engine.snapshot("notes", "delete", label="Before deleting “Groceries”")
        self.store.delete("notes", "groceries")
        self.store.set("notes", "ideas", {"title": "Ideas", "body": "an OS with Rewind"})
        self.assertNotIn("groceries", self.values("notes"))

        result = self.engine.rewind_app("notes", snap["id"])
        self.assertFalse(result["noop"])
        vals = self.values("notes")
        self.assertEqual(vals["groceries"]["body"], "milk")
        self.assertEqual(vals["ideas"]["body"], "an OS")

        # The rewind itself is undoable.
        self.engine.rewind_app("notes", result["undo"])
        vals = self.values("notes")
        self.assertNotIn("groceries", vals)
        self.assertEqual(vals["ideas"]["body"], "an OS with Rewind")

    def test_rewinding_one_app_leaves_others_alone(self):
        self.store.set("notes", "n", "v1")
        self.store.set("sketch", "s", "v1")
        snap, _ = self.engine.snapshot("notes", "manual")
        self.store.set("notes", "n", "v2")
        self.store.set("sketch", "s", "v2")
        self.engine.rewind_app("notes", snap["id"])
        self.assertEqual(self.values("notes")["n"], "v1")
        self.assertEqual(self.values("sketch")["s"], "v2")

    def test_snapshots_can_be_read_without_restoring(self):
        self.store.set("notes", "n", "past")
        snap, _ = self.engine.snapshot("notes", "auto")
        self.store.set("notes", "n", "present")
        base = self.engine.snapshot_path("notes", snap["id"])
        self.assertEqual(self.store.get("notes", "n", base=base)["value"], "past")
        self.assertEqual(self.store.get("notes", "n")["value"], "present")

    def test_rewind_to_current_state_is_a_noop(self):
        self.store.set("notes", "n", 1)
        snap, _ = self.engine.snapshot("notes", "auto")
        result = self.engine.rewind_app("notes", snap["id"])
        self.assertTrue(result["noop"])

    def test_delete_label_attaches_to_unchanged_auto_snapshot(self):
        self.store.set("notes", "n", 1)
        auto, _ = self.engine.snapshot("notes", "auto")
        labeled, created = self.engine.snapshot("notes", "delete", label="Before deleting “n”")
        self.assertFalse(created)
        self.assertEqual(labeled["id"], auto["id"])
        self.assertEqual(self.engine.get_snapshot("notes", auto["id"])["label"],
                         "Before deleting “n”")

    def test_phone_points_only_when_something_changed(self):
        self.store.set("notes", "n", 1)
        p1 = self.engine.checkpoint_phone("auto")
        self.assertIsNotNone(p1)
        self.clock.advance(60)
        self.assertIsNone(self.engine.checkpoint_phone("auto"))
        self.store.set("sketch", "s", 1)
        self.clock.advance(60)
        p2 = self.engine.checkpoint_phone("auto")
        self.assertEqual(p2["changed"], ["sketch"])
        # notes didn't change, so the point reuses its earlier snapshot
        self.assertEqual(p2["members"]["notes"], p1["members"]["notes"])

    def test_snapshots_taken_between_points_still_make_a_point(self):
        self.store.set("notes", "n", 1)
        self.engine.checkpoint_phone("auto")
        self.clock.advance(20)
        self.store.set("notes", "n", 2)
        closed, _ = self.engine.snapshot("notes", "close")  # user left the app
        self.clock.advance(20)
        point = self.engine.checkpoint_phone("auto")
        self.assertIsNotNone(point)
        self.assertEqual(point["changed"], ["notes"])
        self.assertEqual(point["members"]["notes"], closed["id"])

    def test_whole_phone_rewind(self):
        self.store.set("notes", "n", "morning")
        self.store.set("sketch", "s", "morning")
        point = self.engine.checkpoint_phone("manual", label="Morning")
        self.clock.advance(3600)
        self.store.set("notes", "n", "evening")
        self.store.delete("sketch", "s")
        result = self.engine.rewind_phone(point["id"])
        self.assertEqual(sorted(result["apps"]), ["notes", "sketch"])
        self.assertEqual(self.values("notes")["n"], "morning")
        self.assertEqual(self.values("sketch")["s"], "morning")
        # and undo
        self.engine.rewind_phone(result["undo"])
        self.assertEqual(self.values("notes")["n"], "evening")
        self.assertNotIn("s", self.values("sketch"))

    def test_prune_keeps_recent_and_referenced(self):
        for i in range(30):
            self.store.set("notes", "n", i)
            self.engine.checkpoint_phone("auto")
            self.clock.advance(10 * 60)  # every 10 minutes for 5 hours
        before = len(self.engine.list_snapshots("notes"))
        removed = self.engine.prune()
        after = self.engine.list_snapshots("notes")
        self.assertEqual(before - removed["snapshots"], len(after))
        self.assertLess(len(after), before)
        # newest data must still be restorable
        self.assertEqual(after[-1]["id"], self.engine.latest("notes")["id"])
        for snap in after:
            self.assertTrue(self.engine.snapshot_path("notes", snap["id"]).exists())

    def test_events_are_published(self):
        sub = self.bus.subscribe()
        self.store.set("notes", "n", 1)
        snap, _ = self.engine.snapshot("notes", "auto")
        self.store.set("notes", "n", 2)
        self.engine.rewind_app("notes", snap["id"])
        kinds = []
        while not sub.empty():
            kinds.append(sub.get_nowait()["type"])
        self.assertIn("snapshot", kinds)
        self.assertIn("rewound", kinds)


class CopyBackendTests(EngineTestsMixin, unittest.TestCase):
    backend_cls = CopyBackend

    def make_root(self) -> Path:
        tmp = tempfile.mkdtemp(prefix="hindsight-copy-")
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        return Path(tmp)


def btrfs_available() -> bool:
    return (
        sys.platform.startswith("linux")
        and hasattr(os, "geteuid") and os.geteuid() == 0
        and shutil.which("mkfs.btrfs") is not None
        and shutil.which("btrfs") is not None
        and os.environ.get("HINDSIGHT_SKIP_BTRFS") != "1"
    )


@unittest.skipUnless(btrfs_available(), "needs root, Linux and btrfs-progs")
class BtrfsBackendTests(EngineTestsMixin, unittest.TestCase):
    backend_cls = BtrfsBackend
    _image = _mount = None

    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.mkdtemp(prefix="hindsight-btrfs-")
        cls._image = os.path.join(cls._tmp, "fs.img")
        cls._mount = os.path.join(cls._tmp, "mnt")
        os.mkdir(cls._mount)
        with open(cls._image, "wb") as fh:
            fh.truncate(512 * 1024 * 1024)
        subprocess.run(["mkfs.btrfs", "-q", cls._image], check=True)
        subprocess.run(["mount", "-o", "loop", cls._image, cls._mount], check=True)

    @classmethod
    def tearDownClass(cls):
        subprocess.run(["umount", cls._mount], check=False)
        shutil.rmtree(cls._tmp, ignore_errors=True)

    def make_root(self) -> Path:
        root = Path(tempfile.mkdtemp(dir=self._mount))
        return root

    def test_volumes_are_subvolumes_and_snapshots_are_read_only(self):
        self.store.set("notes", "n", 1)
        snap, _ = self.engine.snapshot("notes", "auto")
        self.assertTrue(BtrfsBackend.is_subvolume(self.store.volume("notes")))
        path = self.engine.snapshot_path("notes", snap["id"])
        self.assertTrue(BtrfsBackend.is_subvolume(path))
        with self.assertRaises(OSError):
            (path / "kv" / "intruder.json").write_text("{}")

    def test_plain_folder_is_converted_to_subvolume(self):
        legacy = self.store.volume("legacy")
        (legacy / "kv").mkdir(parents=True)
        (legacy / "kv" / f"{encode_key('k')}.json").write_text(
            '{"key": "k", "value": 42, "updated": 0}')
        self.assertFalse(BtrfsBackend.is_subvolume(legacy))
        self.store.ensure("legacy")
        self.assertTrue(BtrfsBackend.is_subvolume(legacy))
        self.assertEqual(self.store.get("legacy", "k")["value"], 42)


if __name__ == "__main__":
    unittest.main()
