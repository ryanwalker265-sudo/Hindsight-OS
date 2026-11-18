"""Storage backends for Rewind.

A backend knows how to create an app's data volume, take a read-only
snapshot of it, put a snapshot back, and delete snapshots.

* BtrfsBackend  - the real thing. Every app's data is its own Btrfs
  subvolume, so a snapshot is instant and only stores what changes.
* CopyBackend   - a plain-directory fallback for development machines
  (Windows, macOS, or a Linux folder that isn't on Btrfs). Same behaviour,
  just slower and without the space savings.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import time
from pathlib import Path


class BackendError(RuntimeError):
    pass


class Backend:
    name = "base"

    def ensure_volume(self, path: Path) -> None:
        raise NotImplementedError

    def snapshot(self, src: Path, dst: Path) -> None:
        raise NotImplementedError

    def restore(self, snap: Path, dst: Path) -> None:
        raise NotImplementedError

    def delete(self, path: Path) -> None:
        raise NotImplementedError

    def describe(self) -> dict:
        return {"name": self.name}


def _trash_name(dst: Path) -> Path:
    return dst.parent / f".trash-{dst.name}-{time.time_ns()}"


class CopyBackend(Backend):
    """Snapshots are full directory copies. Works on any filesystem."""

    name = "copy"

    def ensure_volume(self, path: Path) -> None:
        path.mkdir(parents=True, exist_ok=True)

    def snapshot(self, src: Path, dst: Path) -> None:
        dst.parent.mkdir(parents=True, exist_ok=True)
        tmp = dst.parent / f".partial-{dst.name}"
        if tmp.exists():
            shutil.rmtree(tmp)
        shutil.copytree(src, tmp)
        os.replace(tmp, dst)

    def restore(self, snap: Path, dst: Path) -> None:
        staging = dst.parent / f".restore-{dst.name}-{time.time_ns()}"
        shutil.copytree(snap, staging)
        trash = _trash_name(dst)
        if dst.exists():
            os.replace(dst, trash)
        try:
            os.replace(staging, dst)
        except Exception:
            if trash.exists():
                os.replace(trash, dst)
            raise
        if trash.exists():
            shutil.rmtree(trash, ignore_errors=True)

    def delete(self, path: Path) -> None:
        if path.exists():
            shutil.rmtree(path)

    def describe(self) -> dict:
        return {
            "name": self.name,
            "summary": "Plain copies (development mode, not space-efficient)",
        }


class BtrfsBackend(Backend):
    """Each app volume is a Btrfs subvolume; snapshots are copy-on-write."""

    name = "btrfs"

    def __init__(self, btrfs_bin: str = "btrfs"):
        self.btrfs = btrfs_bin

    def _run(self, *args: str) -> str:
        proc = subprocess.run(
            [self.btrfs, *args],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        if proc.returncode != 0:
            raise BackendError(
                f"btrfs {' '.join(args)} failed: {proc.stderr.strip() or proc.stdout.strip()}"
            )
        return proc.stdout

    @staticmethod
    def is_subvolume(path: Path) -> bool:
        # The root directory of every Btrfs subvolume has inode number 256.
        try:
            return path.is_dir() and os.stat(path).st_ino == 256
        except OSError:
            return False

    def ensure_volume(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        if self.is_subvolume(path):
            return
        if not path.exists():
            self._run("subvolume", "create", str(path))
            return
        # A plain directory is in the way (e.g. data created in dev mode):
        # convert it into a subvolume, keeping its contents.
        staging = path.parent / f".convert-{path.name}-{time.time_ns()}"
        self._run("subvolume", "create", str(staging))
        for child in path.iterdir():
            shutil.move(str(child), str(staging / child.name))
        path.rmdir()
        os.replace(staging, path)

    def snapshot(self, src: Path, dst: Path) -> None:
        dst.parent.mkdir(parents=True, exist_ok=True)
        self._run("subvolume", "snapshot", "-r", str(src), str(dst))

    def restore(self, snap: Path, dst: Path) -> None:
        trash = _trash_name(dst)
        moved = False
        if dst.exists():
            os.replace(dst, trash)  # renaming a subvolume is instant
            moved = True
        try:
            # A writable snapshot of the read-only snapshot becomes the live data.
            self._run("subvolume", "snapshot", str(snap), str(dst))
        except Exception:
            if moved:
                os.replace(trash, dst)
            raise
        if moved:
            self.delete(trash)

    def delete(self, path: Path) -> None:
        if not path.exists():
            return
        if self.is_subvolume(path):
            self._run("subvolume", "delete", str(path))
        else:
            shutil.rmtree(path)

    def describe(self) -> dict:
        return {
            "name": self.name,
            "summary": "Btrfs copy-on-write snapshots (instant, only changes use space)",
        }


def filesystem_type(path: Path) -> str | None:
    """Return the filesystem type that `path` lives on (Linux only)."""
    try:
        target = os.path.realpath(path)
        best, best_type = "", None
        with open("/proc/self/mountinfo", encoding="utf-8") as fh:
            for line in fh:
                left, _, right = line.partition(" - ")
                fields = left.split()
                if len(fields) < 5:
                    continue
                mount_point = fields[4].replace("\\040", " ")
                fs_type = right.split()[0] if right else None
                if (
                    target == mount_point
                    or target.startswith(mount_point.rstrip("/") + "/")
                    or mount_point == "/"
                ) and len(mount_point) >= len(best):
                    best, best_type = mount_point, fs_type
        return best_type
    except OSError:
        return None


def detect_backend(data_root: Path, preferred: str | None = None) -> Backend:
    """Pick Btrfs when the data folder is on Btrfs and the tools exist."""
    preferred = (preferred or os.environ.get("HINDSIGHT_BACKEND") or "auto").lower()
    if preferred == "copy":
        return CopyBackend()
    btrfs_bin = shutil.which("btrfs")
    on_btrfs = filesystem_type(data_root) == "btrfs"
    if preferred == "btrfs":
        if not (btrfs_bin and on_btrfs):
            raise BackendError(
                f"Btrfs backend requested but {data_root} is not on Btrfs "
                "or the btrfs tool is missing"
            )
        return BtrfsBackend(btrfs_bin)
    if btrfs_bin and on_btrfs and hasattr(os, "geteuid") and os.geteuid() == 0:
        return BtrfsBackend(btrfs_bin)
    return CopyBackend()
