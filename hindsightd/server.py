"""hindsightd's HTTP server: serves the phone interface, apps, and the API.

Everything listens on 127.0.0.1 only; the phone's own browser engine is
the only client.
"""
from __future__ import annotations

import json
import mimetypes
import os
import platform
import queue
import socket
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlsplit

from . import __version__
from .apps import AppRegistry
from .backends import BackendError, detect_backend
from .events import EventBus
from .rewind import NotFound, RewindEngine, Scheduler
from .store import AppStore, StoreError, check_app_id

MAX_BODY = 9 * 1024 * 1024
INTERVAL_CHOICES = (30, 60, 300, 900)

mimetypes.add_type("text/javascript", ".js")
mimetypes.add_type("text/javascript", ".mjs")
mimetypes.add_type("image/svg+xml", ".svg")
mimetypes.add_type("font/woff2", ".woff2")
mimetypes.add_type("application/manifest+json", ".webmanifest")


class HttpError(Exception):
    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status


class Hindsight:
    """Wires the store, Rewind engine, scheduler and app registry together."""

    def __init__(self, data_root, app_dirs, shell_dir, sdk_dir, backend=None,
                 default_interval=60, log=print):
        self.started = time.time()
        self.data_root = Path(data_root)
        self.data_root.mkdir(parents=True, exist_ok=True)
        self.shell_dir = Path(shell_dir)
        self.sdk_dir = Path(sdk_dir)
        self.backend = detect_backend(self.data_root, backend)
        self.store = AppStore(self.data_root, self.backend)
        self.registry = AppRegistry(app_dirs)
        self.events = EventBus()
        self.engine = RewindEngine(self.store, self.registry.ids, self.events)
        self.default_interval = default_interval
        self.log = log
        self.scheduler = Scheduler(self.engine, self.interval, log=log)
        for app_id in self.registry.ids():
            self.store.ensure(app_id)

    def interval(self) -> int:
        rec = self.store.get("settings", "rewind.interval") if self.registry.get("settings") else None
        try:
            value = int(rec["value"]) if rec else self.default_interval
        except (TypeError, ValueError, KeyError):
            value = self.default_interval
        return value if value in INTERVAL_CHOICES else self.default_interval

    def start_scheduler(self) -> None:
        self.scheduler.start()


def make_handler(hs: Hindsight):
    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"
        server_version = f"hindsightd/{__version__}"

        # ---------------------------------------------------------- plumbing
        def log_message(self, fmt, *args):  # quiet by default
            if os.environ.get("HINDSIGHT_HTTP_LOG"):
                super().log_message(fmt, *args)

        def _send(self, status: int, body: bytes, ctype: str, extra=None):
            self.send_response(status)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            for k, v in (extra or {}).items():
                self.send_header(k, v)
            self.end_headers()
            if self.command != "HEAD":
                self.wfile.write(body)

        def _json(self, data, status: int = 200):
            body = json.dumps(data, ensure_ascii=False).encode("utf-8")
            self._send(status, body, "application/json; charset=utf-8")

        def _body(self) -> dict:
            length = int(self.headers.get("Content-Length") or 0)
            if length > MAX_BODY:
                raise HttpError(413, "request body too large")
            if length == 0:
                return {}
            try:
                data = json.loads(self.rfile.read(length).decode("utf-8"))
            except ValueError:
                raise HttpError(400, "body must be JSON")
            if not isinstance(data, dict):
                raise HttpError(400, "body must be a JSON object")
            return data

        def _static(self, base: Path, rel: str):
            base = base.resolve()
            target = (base / rel).resolve()
            if base != target and base not in target.parents:
                raise HttpError(404, "not found")
            if target.is_dir():
                target = target / "index.html"
            if not target.is_file():
                raise HttpError(404, "not found")
            ctype = mimetypes.guess_type(target.name)[0] or "application/octet-stream"
            if ctype.startswith("text/") or ctype in ("application/json", "image/svg+xml"):
                ctype += "; charset=utf-8"
            self._send(200, target.read_bytes(), ctype)

        def _dispatch(self):
            url = urlsplit(self.path)
            parts = [unquote(p) for p in url.path.split("/") if p]
            query = {k: v[-1] for k, v in parse_qs(url.query).items()}
            try:
                if parts and parts[0] == "api":
                    self._api(self.command, parts[1:], query)
                elif self.command not in ("GET", "HEAD"):
                    raise HttpError(405, "method not allowed")
                elif not parts or parts == ["index.html"]:
                    self._static(hs.shell_dir, "index.html")
                elif parts[0] == "shell":
                    self._static(hs.shell_dir, "/".join(parts[1:]))
                elif parts[0] == "sdk":
                    self._static(hs.sdk_dir, "/".join(parts[1:]))
                elif parts[0] == "apps" and len(parts) >= 2:
                    app = hs.registry.get(parts[1])
                    if not app:
                        raise HttpError(404, "no such app")
                    self._static(app.dir, "/".join(parts[2:]) or app.entry)
                elif parts == ["favicon.ico"]:
                    self._send(204, b"", "image/x-icon")
                else:
                    raise HttpError(404, "not found")
            except HttpError as exc:
                self._json({"error": str(exc)}, exc.status)
            except NotFound as exc:
                self._json({"error": str(exc).strip("'\"")}, 404)
            except StoreError as exc:
                self._json({"error": str(exc)}, 400)
            except BackendError as exc:
                hs.log(f"backend error: {exc}")
                self._json({"error": str(exc)}, 500)
            except (BrokenPipeError, ConnectionResetError):
                pass

        do_GET = do_HEAD = do_PUT = do_POST = do_DELETE = _dispatch

        # --------------------------------------------------------------- API
        def _app(self, app_id: str):
            check_app_id(app_id)
            app = hs.registry.get(app_id)
            if not app:
                raise HttpError(404, f"no app called {app_id}")
            return app

        def _snapshot_base(self, app_id: str, query: dict):
            at = query.get("at")
            if not at:
                return None
            hs.engine.get_snapshot(app_id, at)  # raises NotFound
            return hs.engine.snapshot_path(app_id, at)

        def _api(self, method: str, p: list[str], q: dict):
            eng, store = hs.engine, hs.store

            if p == ["events"] and method == "GET":
                return self._sse(q.get("app"))

            if p == ["system"] and method == "GET":
                apps = hs.registry.scan()
                snaps = sum(len(eng.list_snapshots(a)) for a in apps)
                return self._json({
                    "name": "Hindsight",
                    "version": __version__,
                    "backend": hs.backend.describe(),
                    "data": str(hs.data_root),
                    "interval": hs.interval(),
                    "intervalChoices": list(INTERVAL_CHOICES),
                    "apps": len(apps),
                    "snapshots": snaps,
                    "points": len(eng.list_points()),
                    "uptime": time.time() - hs.started,
                    "host": socket.gethostname(),
                    "platform": platform.platform(terse=True),
                    "now": time.time(),
                })

            if p == ["apps"] and method == "GET":
                out = []
                for app in sorted(hs.registry.scan().values(), key=lambda a: (a.order, a.name)):
                    info = app.public()
                    snaps = eng.list_snapshots(app.id)
                    info["snapshots"] = len(snaps)
                    info["latest"] = snaps[-1]["created"] if snaps else None
                    out.append(info)
                return self._json({"apps": out})

            if p == ["points"]:
                if method == "GET":
                    return self._json({"points": eng.list_points()})
                if method == "POST":
                    body = self._body()
                    point = eng.checkpoint_phone("manual", label=body.get("label") or "Checkpoint")
                    return self._json({"point": point}, 201)

            if len(p) == 3 and p[0] == "points" and p[2] == "rewind" and method == "POST":
                return self._json(eng.rewind_phone(p[1]))

            if p == ["system", "prune"] and method == "POST":
                return self._json(eng.prune())

            if len(p) >= 3 and p[0] == "apps":
                app = self._app(p[1])
                rest = p[2:]

                if rest == ["kv"] and method == "GET":
                    base = self._snapshot_base(app.id, q)
                    recs = store.records(app.id, base=base)
                    return self._json({"app": app.id, "at": q.get("at"), "records": recs})

                if len(rest) == 2 and rest[0] == "kv":
                    key = rest[1]
                    if method == "GET":
                        rec = store.get(app.id, key, base=self._snapshot_base(app.id, q))
                        if rec is None:
                            if q.get("missing") == "ok":  # SDK asks for a quiet miss
                                return self._json({"key": key, "value": None, "missing": True})
                            raise HttpError(404, "no such key")
                        return self._json(rec)
                    if method == "PUT":
                        body = self._body()
                        if "value" not in body:
                            raise HttpError(400, 'body needs a "value"')
                        rec = store.set(app.id, key, body["value"])
                        hs.events.publish({"type": "changed", "app": app.id, "key": key})
                        if app.id == "settings" and key == "rewind.interval":
                            hs.scheduler.kick()
                        return self._json(rec)
                    if method == "DELETE":
                        label = q.get("label") or "Before a delete"
                        with store.lock(app.id):
                            if store.get(app.id, key) is None:
                                raise HttpError(404, "no such key")
                            eng.snapshot(app.id, trigger="delete", label=label[:120])
                            store.delete(app.id, key)
                        hs.events.publish({"type": "changed", "app": app.id, "key": key,
                                           "deleted": True})
                        return self._json({"deleted": key})

                if rest == ["snapshots"] and method == "GET":
                    return self._json({
                        "app": app.id,
                        "snapshots": eng.list_snapshots(app.id),
                        # lets the dial hide a snapshot that is identical to "now"
                        "current": store.fingerprint(app.id),
                        "dirty": store.is_dirty(app.id),
                    })

                if rest == ["checkpoint"] and method == "POST":
                    body = self._body()
                    label = str(body.get("label") or "Checkpoint")[:120]
                    meta, created = eng.snapshot(app.id, trigger="checkpoint", label=label)
                    return self._json({"snapshot": meta, "created": created})

                if rest == ["closed"] and method == "POST":
                    meta, created = eng.snapshot(app.id, trigger="close")
                    return self._json({"snapshot": meta, "created": created})

                if rest == ["rewind"] and method == "POST":
                    body = self._body()
                    sid = body.get("snapshot")
                    if not sid:
                        raise HttpError(400, 'body needs a "snapshot" id')
                    return self._json(eng.rewind_app(app.id, sid))

            raise HttpError(404, "unknown API route")

        def _sse(self, app_filter: str | None):
            sub = hs.events.subscribe()
            try:
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.send_header("Cache-Control", "no-store")
                self.send_header("Connection", "close")
                self.end_headers()
                self.close_connection = True
                self.wfile.write(b"retry: 2000\n\n")
                self.wfile.flush()
                while True:
                    try:
                        event = sub.get(timeout=15)
                    except queue.Empty:
                        self.wfile.write(b": ping\n\n")
                        self.wfile.flush()
                        continue
                    if app_filter and event.get("app") not in (None, app_filter):
                        continue
                    payload = json.dumps(event, ensure_ascii=False)
                    self.wfile.write(f"data: {payload}\n\n".encode("utf-8"))
                    self.wfile.flush()
            except (BrokenPipeError, ConnectionResetError, OSError):
                pass
            finally:
                hs.events.unsubscribe(sub)

    return Handler


class Server(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True


def serve(hs: Hindsight, host: str = "127.0.0.1", port: int = 7070) -> Server:
    return Server((host, port), make_handler(hs))
