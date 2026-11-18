"""Per-app key/value storage.

Each app gets its own data volume:

    <data>/apps/<app-id>/kv/<encoded-key>.json

One small file per key keeps snapshots cheap: Btrfs only has to track the
files that actually changed between two points in time.
"""
from __future__ import annotations

import base64
import json
import os
import re
import threading
import time
import zlib
from pathlib import Path

MAX_KEY_BYTES = 150
MAX_VALUE_BYTES = 8 * 1024 * 1024
APP_ID_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,31}$")


class StoreError(ValueError):
    pass


def encode_key(key: str) -> str:
    if not isinstance(key, str) or not key:
        raise StoreError("key must be a non-empty string")
    raw = key.encode("utf-8")
    if len(raw) > MAX_KEY_BYTES:
        raise StoreError(f"key is longer than {MAX_KEY_BYTES} bytes")
    # base32 is case-insensitive, so this is safe on Windows/macOS too.
    return base64.b32encode(raw).decode("ascii").rstrip("=").lower()


def decode_key(name: str) -> str:
    stem = name[:-5] if name.endswith(".json") else name
    padded = stem.upper() + "=" * (-len(stem) % 8)
    return base64.b32decode(padded).decode("utf-8")


def check_app_id(app_id: str) -> str:
    if not isinstance(app_id, str) or not APP_ID_RE.match(app_id):
        raise StoreError(f"invalid app id: {app_id!r}")
    return app_id


class AppStore:
    def __init__(self, data_root: Path, backend):
        self.root = Path(data_root)
        self.backend = backend
        self.apps_dir = self.root / "apps"
        self._locks: dict[str, threading.RLock] = {}
        self._locks_guard = threading.Lock()
        # Apps written since their last snapshot. Belt-and-braces next to
        # fingerprint(): file timestamps can be too coarse to see two quick
        # same-size writes.
        self._dirty: set[str] = set()

    # -- locking ---------------------------------------------------------
    def lock(self, app_id: str) -> threading.RLock:
        with self._locks_guard:
            if app_id not in self._locks:
                self._locks[app_id] = threading.RLock()
            return self._locks[app_id]

    # -- paths -----------------------------------------------------------
    def volume(self, app_id: str) -> Path:
        return self.apps_dir / check_app_id(app_id)

    def ensure(self, app_id: str) -> Path:
        vol = self.volume(app_id)
        with self.lock(app_id):
            self.backend.ensure_volume(vol)
            (vol / "kv").mkdir(exist_ok=True)
        return vol

    # -- reads -----------------------------------------------------------
    @staticmethod
    def _read_kv_dir(kv_dir: Path) -> dict:
        out = {}
        if not kv_dir.is_dir():
            return out
        for entry in sorted(kv_dir.iterdir()):
            if not entry.name.endswith(".json") or entry.name.startswith("."):
                continue
            try:
                with open(entry, encoding="utf-8") as fh:
                    rec = json.load(fh)
                out[rec["key"]] = rec
            except (OSError, ValueError, KeyError):
                continue  # half-written or foreign file: skip it
        return out

    def records(self, app_id: str, base: Path | None = None) -> dict:
        """All records for an app. `base` reads a snapshot instead of live data."""
        vol = base if base is not None else self.volume(app_id)
        return self._read_kv_dir(vol / "kv")

    def get(self, app_id: str, key: str, base: Path | None = None):
        vol = base if base is not None else self.volume(app_id)
        path = vol / "kv" / f"{encode_key(key)}.json"
        try:
            with open(path, encoding="utf-8") as fh:
                return json.load(fh)
        except FileNotFoundError:
            return None

    # -- writes ----------------------------------------------------------
    def set(self, app_id: str, key: str, value) -> dict:
        payload = json.dumps(
            {"key": key, "value": value, "updated": time.time()},
            ensure_ascii=False,
            separators=(",", ":"),
        )
        if len(payload.encode("utf-8")) > MAX_VALUE_BYTES:
            raise StoreError("value is too large (8 MB max)")
        name = encode_key(key)
        with self.lock(app_id):
            vol = self.ensure(app_id)
            kv = vol / "kv"
            tmp = kv / f".tmp-{name}-{threading.get_ident()}"
            with open(tmp, "w", encoding="utf-8") as fh:
                fh.write(payload)
                fh.flush()
                os.fsync(fh.fileno())
            os.replace(tmp, kv / f"{name}.json")
            self._dirty.add(app_id)
        return json.loads(payload)

    def delete(self, app_id: str, key: str) -> bool:
        with self.lock(app_id):
            path = self.volume(app_id) / "kv" / f"{encode_key(key)}.json"
            try:
                path.unlink()
                self._dirty.add(app_id)
                return True
            except FileNotFoundError:
                return False

    def is_dirty(self, app_id: str) -> bool:
        return app_id in self._dirty

    def clear_dirty(self, app_id: str) -> None:
        self._dirty.discard(app_id)

    # -- change detection -------------------------------------------------
    def fingerprint(self, app_id: str, base: Path | None = None) -> str:
        """Cheap signature of an app's data; changes whenever data changes."""
        vol = base if base is not None else self.volume(app_id)
        kv = vol / "kv"
        if not kv.is_dir():
            return "empty"
        count, total, newest = 0, 0, 0
        for entry in os.scandir(kv):
            if entry.name.startswith(".") or not entry.name.endswith(".json"):
                continue
            st = entry.stat()
            count += 1
            total += st.st_size
            newest = max(newest, st.st_mtime_ns)
            # Fold in names so a same-size rename/replace still registers.
            total += zlib.crc32(entry.name.encode("ascii")) & 0xFFFF
        return f"{count}:{total}:{newest}"

    def stats(self, app_id: str, base: Path | None = None) -> dict:
        vol = base if base is not None else self.volume(app_id)
        kv = vol / "kv"
        count, size = 0, 0
        if kv.is_dir():
            for entry in os.scandir(kv):
                if entry.name.endswith(".json") and not entry.name.startswith("."):
                    count += 1
                    size += entry.stat().st_size
        return {"keys": count, "bytes": size}
