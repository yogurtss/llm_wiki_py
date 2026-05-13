from __future__ import annotations

import json
import re
import uuid
from pathlib import Path

from app.models import WikiProject
from app.storage import SQLiteStore


PROJECT_META = ".llm-wiki/project.json"


class ProjectService:
    def __init__(self, workspace: Path, store: SQLiteStore):
        self.workspace = workspace.resolve()
        self.store = store
        self.workspace.mkdir(parents=True, exist_ok=True)

    def create_project(self, name: str) -> WikiProject:
        clean_name = name.strip() or "Untitled Wiki"
        slug = self._slugify(clean_name)
        path = self._unique_project_path(slug)
        project = WikiProject(id=str(uuid.uuid4()), name=clean_name, path=path)
        self._create_structure(project)
        self.store.upsert_project(project)
        return project

    def open_project(self, path: str | Path) -> WikiProject:
        project_path = Path(path).expanduser().resolve()
        if not project_path.exists() or not project_path.is_dir():
            raise FileNotFoundError(f"Project directory does not exist: {project_path}")
        project = self._load_or_create_identity(project_path)
        self._ensure_required_dirs(project.path)
        self.store.upsert_project(project)
        return project

    def recent_projects(self) -> list[WikiProject]:
        return self.store.list_recent_projects()

    def get_project(self, project_id: str) -> WikiProject | None:
        return self.store.get_project(project_id)

    def _create_structure(self, project: WikiProject) -> None:
        project.path.mkdir(parents=True, exist_ok=False)
        self._ensure_required_dirs(project.path)
        self._write_project_identity(project)
        self._write_if_missing(project.path / "schema.md", DEFAULT_SCHEMA)
        self._write_if_missing(project.path / "purpose.md", DEFAULT_PURPOSE.format(name=project.name))
        self._write_if_missing(project.path / "wiki/index.md", DEFAULT_INDEX.format(name=project.name))
        self._write_if_missing(project.path / "wiki/log.md", "# Wiki Log\n\n")

    def _load_or_create_identity(self, path: Path) -> WikiProject:
        meta_path = path / PROJECT_META
        if meta_path.exists():
            try:
                raw = json.loads(meta_path.read_text(encoding="utf-8"))
                project = WikiProject(
                    id=str(raw["id"]),
                    name=str(raw.get("name") or path.name),
                    path=path,
                )
                self._write_project_identity(project)
                return project
            except (OSError, json.JSONDecodeError, KeyError, TypeError):
                pass
        project = WikiProject(id=str(uuid.uuid4()), name=path.name, path=path)
        self._write_project_identity(project)
        return project

    def _write_project_identity(self, project: WikiProject) -> None:
        meta_path = project.path / PROJECT_META
        meta_path.parent.mkdir(parents=True, exist_ok=True)
        meta_path.write_text(
            json.dumps(project.to_dict(), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def _ensure_required_dirs(self, path: Path) -> None:
        for rel in ("wiki", "raw/sources", ".llm-wiki"):
            (path / rel).mkdir(parents=True, exist_ok=True)

    def _write_if_missing(self, path: Path, contents: str) -> None:
        if not path.exists():
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(contents, encoding="utf-8")

    def _unique_project_path(self, slug: str) -> Path:
        path = self.workspace / slug
        if not path.exists():
            return path
        for index in range(2, 1000):
            candidate = self.workspace / f"{slug}-{index}"
            if not candidate.exists():
                return candidate
        raise RuntimeError("Could not allocate a unique project directory")

    def _slugify(self, value: str) -> str:
        slug = re.sub(r"[^a-zA-Z0-9._-]+", "-", value.strip()).strip("-._")
        return slug.lower() or "untitled-wiki"


DEFAULT_SCHEMA = """# Schema

This file defines how the wiki should organize generated pages.

- Keep source material in `raw/sources/`.
- Keep generated and curated pages in `wiki/`.
- Use YAML frontmatter where useful.
- Use `[[wikilinks]]` for cross references.
"""

DEFAULT_PURPOSE = """# Purpose

This wiki exists to help organize knowledge for **{name}**.

Update this file with goals, scope, open questions, and the direction of the project.
"""

DEFAULT_INDEX = """# {name}

Welcome to your LLM Wiki.

- Start with `purpose.md` to describe what this knowledge base is for.
- Add source files under `raw/sources/`.
- Create or edit Markdown pages under `wiki/`.
"""

