from __future__ import annotations

import json
import threading
from pathlib import Path
from typing import Any


class JsonAppStore:
    """Small replacement for Tauri's plugin-store JSON files."""

    def __init__(self, root: Path) -> None:
        self.root = root
        self.root.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()

    def _path(self, name: str) -> Path:
        clean_name = Path(name).name or "app-state.json"
        return self.root / clean_name

    def _load_all(self, name: str) -> dict[str, Any]:
        path = self._path(name)
        if not path.exists():
            return {}
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}
        return raw if isinstance(raw, dict) else {}

    def _save_all(self, name: str, data: dict[str, Any]) -> None:
        path = self._path(name)
        tmp = path.with_name(f".{path.name}.tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(path)

    def get(self, name: str, key: str, default: Any = None) -> Any:
        with self._lock:
            return self._load_all(name).get(key, default)

    def set(self, name: str, key: str, value: Any) -> None:
        with self._lock:
            data = self._load_all(name)
            data[key] = value
            self._save_all(name, data)

    def delete(self, name: str, key: str) -> None:
        with self._lock:
            data = self._load_all(name)
            data.pop(key, None)
            self._save_all(name, data)

    def save(self, name: str) -> None:
        with self._lock:
            data = self._load_all(name)
            self._save_all(name, data)
