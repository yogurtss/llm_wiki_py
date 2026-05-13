from __future__ import annotations

from typing import Any

from app.storage import SQLiteStore


class SettingsStore:
    def __init__(self, store: SQLiteStore):
        self.store = store

    def get(self, key: str, default: Any = None) -> Any:
        return self.store.get_setting(key, default)

    def set(self, key: str, value: Any) -> None:
        self.store.set_setting(key, value)


DEFAULT_LLM_SETTINGS = {
    "provider": "openai",
    "model": "",
    "api_key": "",
    "base_url": "",
    "max_context_size": 204800,
}

