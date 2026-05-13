from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class AppConfig:
    root: Path
    workspace: Path
    database: Path


def load_config() -> AppConfig:
    root = Path(os.environ.get("LLMWIKI_HOME", Path.cwd() / "data")).resolve()
    workspace = Path(os.environ.get("LLMWIKI_WORKSPACE", root / "projects")).resolve()
    database = Path(os.environ.get("LLMWIKI_DB", root / "llmwiki.sqlite3")).resolve()
    workspace.mkdir(parents=True, exist_ok=True)
    database.parent.mkdir(parents=True, exist_ok=True)
    return AppConfig(root=root, workspace=workspace, database=database)

