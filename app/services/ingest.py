from __future__ import annotations

import re
from datetime import date
from pathlib import Path

from app.models import WikiProject
from app.services.files import FileService
from app.services.markdown import title_from_markdown


class IngestService:
    """Minimal no-LLM source ingestion.

    The original app queues imported sources for LLM ingestion. This Python
    port does not have RAG/LLM wired yet, so import still performs the
    filesystem-side lifecycle work: every source gets a stable wiki source
    page, and index/log are updated. Later LLM ingestion can rewrite or expand
    these pages without changing the surrounding UI flow.
    """

    def __init__(self, files: FileService):
        self.files = files

    def ingest_source(self, project: WikiProject, source_rel_path: str) -> str:
        source_path = self.files.resolve(project, source_rel_path)
        if not source_path.is_file():
            raise FileNotFoundError(source_rel_path)
        source_title = source_path.stem.replace("-", " ").replace("_", " ").strip() or source_path.name
        slug = slugify(source_path.stem)
        wiki_rel = f"wiki/sources/{slug}.md"
        excerpt = self._source_excerpt(project, source_rel_path)
        content = source_summary_content(
            title=source_title,
            source_rel_path=source_rel_path,
            excerpt=excerpt,
        )
        self.files.write_text(project, wiki_rel, content)
        self._ensure_index_entry(project, source_title, slug)
        self._append_log(project, f"Imported source `{source_rel_path}` and created `wiki/sources/{slug}.md`.")
        return wiki_rel

    def delete_source_lifecycle(self, project: WikiProject, source_rel_path: str) -> list[str]:
        deleted: list[str] = []
        source_name = Path(source_rel_path).name
        for rel_path, path in list(self.files.iter_documents(project, ("wiki",))):
            if not rel_path.endswith(".md"):
                continue
            try:
                content = path.read_text(encoding="utf-8")
            except UnicodeDecodeError:
                continue
            if source_rel_path not in content and source_name not in content:
                continue
            if rel_path.startswith("wiki/sources/"):
                self.files.delete(project, rel_path)
                deleted.append(rel_path)
                continue
            rewritten = remove_source_from_frontmatter(content, source_rel_path, source_name)
            if rewritten != content:
                self.files.write_text(project, rel_path, rewritten)
        self._remove_index_entries(project, deleted)
        self._append_log(project, f"Deleted source `{source_rel_path}` and removed {len(deleted)} generated source page(s).")
        return deleted

    def _source_excerpt(self, project: WikiProject, source_rel_path: str) -> str:
        path = self.files.resolve(project, source_rel_path)
        if not self.files.is_text(path):
            return f"`{source_rel_path}` is a non-text source. Content extraction will be handled by a later ingestion phase."
        try:
            raw = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            return f"`{source_rel_path}` could not be decoded as UTF-8."
        title = title_from_markdown(source_rel_path, raw)
        body = raw.strip()
        if len(body) > 1800:
            body = body[:1800].rstrip() + "\n\n..."
        return f"Detected title: **{title}**\n\n```text\n{body}\n```"

    def _ensure_index_entry(self, project: WikiProject, source_title: str, slug: str) -> None:
        index_rel = "wiki/index.md"
        try:
            index = self.files.read_text(project, index_rel)
        except FileNotFoundError:
            index = "# Wiki Index\n"
        entry = f"- [[{source_title}]] - source summary"
        if entry in index:
            return
        section = "## Sources"
        if section not in index:
            index = index.rstrip() + f"\n\n{section}\n\n"
        index = index.rstrip() + f"\n{entry}\n"
        self.files.write_text(project, index_rel, index)

    def _remove_index_entries(self, project: WikiProject, deleted_paths: list[str]) -> None:
        if not deleted_paths:
            return
        try:
            index = self.files.read_text(project, "wiki/index.md")
        except FileNotFoundError:
            return
        deleted_slugs = {Path(path).stem for path in deleted_paths}
        lines = []
        for line in index.splitlines():
            normalized = slugify(line)
            if any(slug in normalized for slug in deleted_slugs):
                continue
            lines.append(line)
        self.files.write_text(project, "wiki/index.md", "\n".join(lines).rstrip() + "\n")

    def _append_log(self, project: WikiProject, message: str) -> None:
        log_rel = "wiki/log.md"
        try:
            log = self.files.read_text(project, log_rel)
        except FileNotFoundError:
            log = "# Wiki Log\n"
        self.files.write_text(project, log_rel, log.rstrip() + f"\n\n- {date.today().isoformat()}: {message}\n")


def source_summary_content(title: str, source_rel_path: str, excerpt: str) -> str:
    return f"""---
title: {title}
type: source
sources:
  - {source_rel_path}
---

# {title}

This source summary was created automatically during import. LLM extraction is not enabled yet, so this page preserves the source link and a lightweight preview.

## Source

- `{source_rel_path}`

## Preview

{excerpt}
"""


def slugify(value: str) -> str:
    value = value.strip().lower()
    value = re.sub(r"[^a-z0-9\u4e00-\u9fff]+", "-", value)
    return value.strip("-") or "source"


def remove_source_from_frontmatter(content: str, source_rel_path: str, source_name: str) -> str:
    lines = content.splitlines()
    out: list[str] = []
    for line in lines:
        if source_rel_path in line or source_name in line:
            continue
        out.append(line)
    return "\n".join(out) + ("\n" if content.endswith("\n") else "")

