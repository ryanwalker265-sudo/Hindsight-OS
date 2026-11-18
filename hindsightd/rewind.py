"""The Rewind engine.

Vocabulary
----------
snapshot  A read-only copy of ONE app's data at a moment in time.
point     A phone-wide moment: for every app, the snapshot that shows what
          that app looked like then. Rewinding the whole phone restores
          every app to its snapshot in the point.

On-disk layout (inside the data folder)
---------------------------------------
apps/<app>/                 live data (a Btrfs subvolume on a real device)
snapshots/<app>/<id>/       read-only snapshots (Btrfs snapshots on a device)
meta/<app>/<id>.json        what each snapshot is (time, label, trigger, size)
meta/_points/<id>.json      phone-wide points

Rewinding never destroys anything: before data is replaced, the current
state is saved as a "Before rewind" snapshot, so every rewind can be undone.
"""
from __future__ import annotations

import json
import os
import re
import threading
import time
from pathlib import Path

from .store import AppStore, check_app_id

ID_RE = re.compile(r"^\d{8}T\d{12}Z$")
HOUR, DAY = 3600, 86400

# Snapshots that mark something meaningful are kept longer.
LABELED_TRIGGERS = {"delete", "manual", "checkpoint", "before-rewind"}


class NotFound(KeyError):
    pass


def check_id(sid: str) -> str:
    if not isinstance(sid, str) or not ID_RE.match(sid):
        raise NotFound(f"invalid snapshot id: {sid!r}")
    return sid


def _write_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.parent / f".tmp-{path.name}-{threading.get_ident()}"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(data, fh, ensure_ascii=False, indent=1)
    os.replace(tmp, path)


def _read_json(path: Path) -> dict | None:
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return None


def thin(items: list[dict], now: float) -> set[str]:
    """Decide which snapshots/points to keep.

    * everything from the last 2 hours
    * labeled moments (deletes, manual checkpoints, pre-rewind) for 7 days
    * one per hour for the last 2 days
    * one per day for the last 30 days
    """
    keep: set[str] = set()
    hour_buckets: set[int] = set()
    day_buckets: set[int] = set()
    for it in sorted(items, key=lambda x: x["created"], reverse=True):
        age = now - it["created"]
        labeled = it.get("trigger") in LABELED_TRIGGERS
        if age < 2 * HOUR or (labeled and age < 7 * DAY):
            keep.add(it["id"])
        elif age < 2 * DAY:
            b = int(it["created"] // HOUR)
            if b not in hour_buckets:
                hour_buckets.add(b)
                keep.add(it["id"])
        elif age < 30 * DAY:
            b = int(it["created"] // DAY)
            if b not in day_buckets:
                day_buckets.add(b)
                keep.add(it["id"])
    return keep


class RewindEngine:
    def __init__(self, store: AppStore, app_ids, events=None, clock=time.time):
        """`app_ids` is a callable returning the ids of installed apps."""
        self.store = store
        self.backend = store.backend
        self.root = store.root
        self.snap_dir = self.root / "snapshots"
        self.meta_dir = self.root / "meta"
        self.points_dir = self.meta_dir / "_points"
        self.app_ids = app_ids
        self.events = events
        self.clock = clock
        self._pass_lock = threading.RLock()
        self._id_lock = threading.Lock()
        self._last_us = 0

    # -- ids & paths -------------------------------------------------------
    def new_id(self) -> str:
        with self._id_lock:
            us = max(int(self.clock() * 1_000_000), self._last_us + 1)
            self._last_us = us
        secs, micro = divmod(us, 1_000_000)
        return time.strftime("%Y%m%dT%H%M%S", time.gmtime(secs)) + f"{micro:06d}Z"

    def snapshot_path(self, app: str, sid: str) -> Path:
        return self.snap_dir / check_app_id(app) / check_id(sid)

    def _meta_path(self, app: str, sid: str) -> Path:
        return self.meta_dir / check_app_id(app) / f"{check_id(sid)}.json"

    def _emit(self, event: dict) -> None:
        if self.events is not None:
            self.events.publish(event)

    # -- listing -----------------------------------------------------------
    def list_snapshots(self, app: str) -> list[dict]:
        folder = self.meta_dir / check_app_id(app)
        out = []
        if folder.is_dir():
            for entry in folder.iterdir():
                if entry.suffix != ".json" or entry.name.startswith("."):
                    continue
                meta = _read_json(entry)
                if meta and self.snapshot_path(app, meta["id"]).exists():
                    out.append(meta)
        out.sort(key=lambda m: m["id"])
        return out

    def get_snapshot(self, app: str, sid: str) -> dict:
        meta = _read_json(self._meta_path(app, sid))
        if not meta or not self.snapshot_path(app, sid).exists():
            raise NotFound(f"{app} has no snapshot {sid}")
        return meta

    def latest(self, app: str) -> dict | None:
        snaps = self.list_snapshots(app)
        return snaps[-1] if snaps else None

    def list_points(self) -> list[dict]:
        out = []
        if self.points_dir.is_dir():
            for entry in self.points_dir.iterdir():
                if entry.suffix != ".json" or entry.name.startswith("."):
                    continue
                point = _read_json(entry)
                if point:
                    out.append(point)
        out.sort(key=lambda p: p["id"])
        return out

    def get_point(self, pid: str) -> dict:
        point = _read_json(self.points_dir / f"{check_id(pid)}.json")
        if not point:
            raise NotFound(f"no phone point {pid}")
        return point

    # -- taking snapshots ----------------------------------------------------
    def snapshot(self, app: str, trigger: str = "manual", label: str | None = None,
                 point: str | None = None, force: bool = False) -> tuple[dict, bool]:
        """Snapshot one app. Returns (snapshot, created).

        If nothing changed since the last snapshot, that snapshot already
        shows the current state, so it is reused instead of duplicated.
        """
        with self.store.lock(app):
            vol = self.store.ensure(app)
            fp = self.store.fingerprint(app)
            last = self.latest(app)
            unchanged = (last is not None and last.get("fingerprint") == fp
                         and not self.store.is_dirty(app))
            if unchanged and not force:
                if label and not last.get("label") and last.get("trigger") in ("auto", "close"):
                    last["label"], last["trigger"] = label, trigger
                    _write_json(self._meta_path(app, last["id"]), last)
                return last, False
            sid = self.new_id()
            self.backend.snapshot(vol, self.snapshot_path(app, sid))
            self.store.clear_dirty(app)
            meta = {
                "id": sid,
                "app": app,
                "created": self.clock(),
                "trigger": trigger,
                "label": label,
                "point": point,
                "fingerprint": fp,
                **self.store.stats(app, base=self.snapshot_path(app, sid)),
            }
            _write_json(self._meta_path(app, sid), meta)
        self._emit({"type": "snapshot", "app": app, "id": sid, "trigger": trigger})
        return meta, True

    def checkpoint_phone(self, trigger: str = "auto", label: str | None = None) -> dict | None:
        """Snapshot every app that changed and record a phone-wide point.

        Automatic passes only record a point when something changed.
        """
        with self._pass_lock:
            points = self.list_points()
            previous = points[-1]["members"] if points else {}
            pid = self.new_id()
            members, changed = {}, []
            for app in sorted(self.app_ids()):
                meta, created = self.snapshot(app, trigger=trigger, point=pid)
                members[app] = meta["id"]
                # Changed since the last point: either we just took a new
                # snapshot, or one was taken in between (on delete, on close...).
                if created or previous.get(app) != meta["id"]:
                    changed.append(app)
            if trigger == "auto" and not changed:
                return None
            point = {
                "id": pid,
                "created": self.clock(),
                "trigger": trigger,
                "label": label,
                "members": members,
                "changed": changed,
            }
            _write_json(self.points_dir / f"{pid}.json", point)
        self._emit({"type": "point", "id": pid, "trigger": trigger, "changed": changed})
        return point

    # -- rewinding ----------------------------------------------------------
    def rewind_app(self, app: str, sid: str) -> dict:
        target = self.get_snapshot(app, sid)
        with self.store.lock(app):
            current_fp = self.store.fingerprint(app)
            if current_fp == target.get("fingerprint") and not self.store.is_dirty(app):
                return {"app": app, "to": sid, "undo": None, "noop": True}
            safety, _ = self.snapshot(app, trigger="before-rewind", label="Before rewind")
            self.backend.restore(self.snapshot_path(app, sid), self.store.volume(app))
        self._emit({"type": "rewound", "app": app, "to": sid, "undo": safety["id"]})
        return {"app": app, "to": sid, "undo": safety["id"], "noop": False}

    def rewind_phone(self, pid: str) -> dict:
        point = self.get_point(pid)
        installed = set(self.app_ids())
        with self._pass_lock:
            safety = self.checkpoint_phone(trigger="before-rewind",
                                           label="Before rewinding the phone")
            restored = []
            for app, sid in sorted(point["members"].items()):
                if app not in installed:
                    continue
                try:
                    target = self.get_snapshot(app, sid)
                except NotFound:
                    continue
                with self.store.lock(app):
                    if self.store.fingerprint(app) == target.get("fingerprint"):
                        continue  # already looks exactly like that point
                    self.backend.restore(self.snapshot_path(app, sid), self.store.volume(app))
                    restored.append(app)
        self._emit({"type": "phone-rewound", "to": pid, "undo": safety["id"],
                    "apps": restored})
        return {"to": pid, "undo": safety["id"], "apps": restored}

    # -- retention ----------------------------------------------------------
    def prune(self, now: float | None = None) -> dict:
        now = self.clock() if now is None else now
        removed_points, removed_snaps = 0, 0
        with self._pass_lock:
            points = self.list_points()
            keep_points = thin(points, now)
            if points:
                keep_points.add(points[-1]["id"])
            referenced: dict[str, set[str]] = {}
            for p in points:
                if p["id"] in keep_points:
                    for app, sid in p["members"].items():
                        referenced.setdefault(app, set()).add(sid)
                else:
                    (self.points_dir / f"{p['id']}.json").unlink(missing_ok=True)
                    removed_points += 1
            if self.snap_dir.is_dir():
                for app_dir in self.snap_dir.iterdir():
                    app = app_dir.name
                    if app.startswith("."):
                        continue
                    with self.store.lock(app):
                        snaps = self.list_snapshots(app)
                        if not snaps:
                            continue
                        keep = thin(snaps, now) | referenced.get(app, set())
                        keep.add(snaps[-1]["id"])
                        for s in snaps:
                            if s["id"] in keep:
                                continue
                            self.backend.delete(self.snapshot_path(app, s["id"]))
                            self._meta_path(app, s["id"]).unlink(missing_ok=True)
                            removed_snaps += 1
        return {"points": removed_points, "snapshots": removed_snaps}


class Scheduler(threading.Thread):
    """Takes a phone-wide checkpoint every few minutes, then prunes."""

    def __init__(self, engine: RewindEngine, interval_fn, log=print):
        super().__init__(name="rewind-scheduler", daemon=True)
        self.engine = engine
        self.interval_fn = interval_fn
        self.log = log
        self._halt = threading.Event()
        self._kick = threading.Event()

    def stop(self) -> None:
        self._halt.set()
        self._kick.set()

    def kick(self) -> None:
        """Run a pass now (e.g. after the interval setting changes)."""
        self._kick.set()

    def run(self) -> None:
        while not self._halt.is_set():
            try:
                point = self.engine.checkpoint_phone("auto")
                if point:
                    self.log(f"rewind: point {point['id']} ({', '.join(point['changed'])})")
                self.engine.prune()
            except Exception as exc:  # keep the scheduler alive no matter what
                self.log(f"rewind: scheduler error: {exc!r}")
            self._kick.wait(timeout=max(5.0, float(self.interval_fn())))
            self._kick.clear()
