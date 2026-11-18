"""Finds installed apps by reading their manifest.json files."""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

from .store import APP_ID_RE


@dataclass
class AppInfo:
    id: str
    name: str
    dir: Path
    entry: str = "index.html"
    icon: str | None = None
    color: str = "#888888"
    description: str = ""
    version: str = "0.0.0"
    order: int = 100
    extra: dict = field(default_factory=dict)

    def public(self) -> dict:
        base = f"/apps/{self.id}/"
        return {
            "id": self.id,
            "name": self.name,
            "url": base + self.entry,
            "icon": base + self.icon if self.icon else None,
            "color": self.color,
            "description": self.description,
            "version": self.version,
            "order": self.order,
        }


class AppRegistry:
    def __init__(self, dirs):
        self.dirs = [Path(d) for d in dirs]

    def scan(self) -> dict[str, AppInfo]:
        found: dict[str, AppInfo] = {}
        for base in self.dirs:
            if not base.is_dir():
                continue
            for manifest in sorted(base.glob("*/manifest.json")):
                try:
                    data = json.loads(manifest.read_text(encoding="utf-8"))
                except (OSError, ValueError):
                    continue
                app_id = data.get("id", "")
                if not APP_ID_RE.match(app_id) or app_id in found:
                    continue
                known = {"id", "name", "entry", "icon", "color", "description",
                         "version", "order"}
                found[app_id] = AppInfo(
                    id=app_id,
                    name=str(data.get("name") or app_id),
                    dir=manifest.parent,
                    entry=str(data.get("entry") or "index.html"),
                    icon=data.get("icon"),
                    color=str(data.get("color") or "#888888"),
                    description=str(data.get("description") or ""),
                    version=str(data.get("version") or "0.0.0"),
                    order=int(data.get("order") or 100),
                    extra={k: v for k, v in data.items() if k not in known},
                )
        return found

    def ids(self) -> list[str]:
        return list(self.scan())

    def get(self, app_id: str) -> AppInfo | None:
        return self.scan().get(app_id)
