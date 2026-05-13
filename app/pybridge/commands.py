from __future__ import annotations

import base64
import hashlib
import json
import mimetypes
import os
import shutil
import subprocess
import threading
import time
import uuid
import webbrowser
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from app.config import AppConfig
from app.pybridge.events import EventHub
from app.pybridge.store import JsonAppStore


EVENT_QUEUE_UPDATED = "file-sync://queue-updated"
EVENT_CHANGED = "file-sync://changed"
SNAPSHOT_FILE = ".llm-wiki/file-snapshot.json"
QUEUE_FILE = ".llm-wiki/file-change-queue.json"


@dataclass
class BridgeContext:
    config: AppConfig
    events: EventHub
    app_store: JsonAppStore


class CommandDispatcher:
    def __init__(self, context: BridgeContext) -> None:
        self.context = context
        self._watcher: PollingWatcher | None = None
        self._commands = {
            "read_file": self.read_file,
            "write_file": self.write_file,
            "list_directory": self.list_directory,
            "copy_file": self.copy_file,
            "copy_directory": self.copy_directory,
            "preprocess_file": self.preprocess_file,
            "delete_file": self.delete_file,
            "find_related_wiki_pages": self.find_related_wiki_pages,
            "create_directory": self.create_directory,
            "file_exists": self.file_exists,
            "read_file_as_base64": self.read_file_as_base64,
            "create_project": self.create_project,
            "open_project": self.open_project,
            "open_project_folder": self.open_project_folder,
            "clip_server_status": lambda: "python bridge running",
            "start_project_file_watcher": self.start_project_file_watcher,
            "stop_project_file_watcher": self.stop_project_file_watcher,
            "rescan_project_files": self.rescan_project_files,
            "get_file_change_queue": self.get_file_change_queue,
            "retry_file_change_task": self.retry_file_change_task,
            "ignore_file_change_task": self.ignore_file_change_task,
            "vector_upsert_chunks": self.vector_upsert_chunks,
            "vector_search_chunks": self.vector_search_chunks,
            "vector_delete_page": self.vector_delete_page,
            "vector_count_chunks": self.vector_count_chunks,
            "vector_legacy_row_count": lambda **_: 0,
            "vector_drop_legacy": lambda **_: None,
            "extract_pdf_images_cmd": lambda **_: [],
            "extract_office_images_cmd": lambda **_: [],
            "extract_and_save_pdf_images_cmd": lambda **_: [],
            "extract_and_save_office_images_cmd": lambda **_: [],
            "claude_cli_detect": self.claude_cli_detect,
            "claude_cli_spawn": self.claude_cli_spawn,
            "claude_cli_kill": lambda **_: None,
            "set_proxy_env": self.set_proxy_env,
            "open_dialog": self.open_dialog,
            "open_url": self.open_url,
            "app_store_get": self.app_store_get,
            "app_store_set": self.app_store_set,
            "app_store_delete": self.app_store_delete,
            "app_store_save": self.app_store_save,
        }

    def invoke(self, command: str, args: dict[str, Any] | None = None) -> Any:
        handler = self._commands.get(command)
        if handler is None:
            raise ValueError(f"Unknown command: {command}")
        return handler(**(args or {}))

    def read_file(self, path: str) -> str:
        p = Path(path)
        ext = p.suffix.lower().lstrip(".")
        if ext in {"png", "jpg", "jpeg", "gif", "webp", "bmp", "ico", "tiff", "tif", "avif", "heic", "heif", "svg"}:
            size = p.stat().st_size if p.exists() else 0
            return f"[Image: {p.name} ({size / 1024:.1f} KB)]"
        if ext in {"mp4", "webm", "mov", "avi", "mkv", "mp3", "wav", "ogg", "flac", "aac", "m4a"}:
            size = p.stat().st_size if p.exists() else 0
            return f"[Media: {p.name} ({size / 1048576:.1f} MB)]"
        if ext in {"pdf", "doc", "docx", "ppt", "pptx", "xls", "xlsx", "pages", "numbers", "key", "epub"}:
            cache = cache_path_for(p)
            if cache.exists() and cache.stat().st_mtime >= p.stat().st_mtime:
                return cache.read_text(encoding="utf-8")
            return f"[Document: {p.name} - text extraction is not enabled in the Python bridge yet]"
        try:
            return p.read_text(encoding="utf-8")
        except FileNotFoundError:
            raise FileNotFoundError(f"File does not exist: '{path}'")
        except UnicodeDecodeError as exc:
            raise ValueError(f"Failed to read file '{path}' as text: {exc}")

    def write_file(self, path: str, contents: str) -> None:
        p = Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(contents, encoding="utf-8")

    def list_directory(self, path: str) -> list[dict[str, Any]]:
        p = Path(path)
        if not p.exists():
            raise FileNotFoundError(f"Path does not exist: '{path}'")
        if not p.is_dir():
            raise NotADirectoryError(f"Path is not a directory: '{path}'")
        return build_tree(p)

    def copy_file(self, source: str, destination: str) -> None:
        dest = Path(destination)
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, destination)

    def copy_directory(self, source: str, destination: str) -> list[str]:
        src = Path(source)
        dest = Path(destination)
        copied: list[str] = []
        for item in src.rglob("*"):
            if not item.is_file():
                continue
            rel = item.relative_to(src)
            target = dest / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(item, target)
            copied.append(normalize_path(target))
        return copied

    def preprocess_file(self, path: str) -> str:
        p = Path(path)
        if p.suffix.lower().lstrip(".") not in {"pdf", "docx", "pptx", "xlsx", "odt", "ods", "odp"}:
            return "no preprocessing needed"
        text = self.read_file(path)
        cache = cache_path_for(p)
        cache.parent.mkdir(parents=True, exist_ok=True)
        cache.write_text(text, encoding="utf-8")
        return text

    def delete_file(self, path: str) -> None:
        p = Path(path)
        if p.is_dir():
            shutil.rmtree(p)
        elif p.exists():
            p.unlink()

    def find_related_wiki_pages(self, projectPath: str, sourceName: str) -> list[str]:
        wiki = Path(projectPath) / "wiki"
        if not wiki.exists():
            return []
        related: list[str] = []
        for path in wiki.rglob("*.md"):
            try:
                if sourceName in path.read_text(encoding="utf-8"):
                    related.append(normalize_path(path))
            except UnicodeDecodeError:
                continue
        return sorted(related)

    def create_directory(self, path: str) -> None:
        Path(path).mkdir(parents=True, exist_ok=True)

    def file_exists(self, path: str) -> bool:
        return Path(path).exists()

    def read_file_as_base64(self, path: str) -> dict[str, str]:
        p = Path(path)
        mime_type = mimetypes.guess_type(p.name)[0] or "application/octet-stream"
        return {
            "base64": base64.b64encode(p.read_bytes()).decode("ascii"),
            "mimeType": mime_type,
        }

    def create_project(self, name: str, path: str) -> dict[str, str]:
        root = Path(path).expanduser().resolve() / name
        if root.exists():
            raise FileExistsError(f"Directory already exists: '{root}'")
        create_project_structure(root, name)
        return {"name": name, "path": normalize_path(root)}

    def open_project(self, path: str) -> dict[str, str]:
        root = Path(path).expanduser().resolve()
        validate_project(root)
        return {"name": root.name or "Unknown", "path": normalize_path(root)}

    def open_project_folder(self, path: str) -> None:
        webbrowser.open(Path(path).resolve().as_uri())

    def start_project_file_watcher(self, projectId: str, projectPath: str) -> dict[str, Any]:
        self.stop_project_file_watcher()
        queue_data = rescan_project(projectId, Path(projectPath), self.context.events)
        watcher = PollingWatcher(projectId, Path(projectPath), self.context.events)
        watcher.start()
        self._watcher = watcher
        return queue_data

    def stop_project_file_watcher(self) -> None:
        if self._watcher is not None:
            self._watcher.stop()
            self._watcher = None

    def rescan_project_files(self, projectId: str, projectPath: str) -> dict[str, Any]:
        return rescan_project(projectId, Path(projectPath), self.context.events)

    def get_file_change_queue(self, projectPath: str) -> dict[str, Any]:
        return load_queue(Path(projectPath))

    def retry_file_change_task(self, projectId: str, projectPath: str, taskId: str) -> dict[str, Any]:
        queue_data = load_queue(Path(projectPath))
        for task in queue_data["tasks"]:
            if task["id"] == taskId and task["projectId"] == projectId:
                task["status"] = "pending"
                task["retryCount"] = int(task.get("retryCount", 0)) + 1
                task["updatedAt"] = now_ms()
                task["error"] = None
        save_queue(Path(projectPath), queue_data)
        emit_queue(self.context.events, projectId, queue_data)
        return queue_data

    def ignore_file_change_task(self, projectId: str, projectPath: str, taskId: str) -> dict[str, Any]:
        queue_data = load_queue(Path(projectPath))
        queue_data["tasks"] = [
            task for task in queue_data["tasks"]
            if not (task["id"] == taskId and task["projectId"] == projectId)
        ]
        save_queue(Path(projectPath), queue_data)
        emit_queue(self.context.events, projectId, queue_data)
        return queue_data

    def vector_upsert_chunks(self, projectPath: str, chunks: list[dict[str, Any]], pageId: str | None = None) -> None:
        data = load_vectors(Path(projectPath))
        for chunk in chunks:
            chunk_index = int(chunk.get("chunk_index", chunk.get("chunkIndex", 0)))
            chunk_page_id = str(pageId or chunk.get("page_id") or chunk.get("pageId") or chunk.get("pagePath") or "")
            chunk_id = str(chunk.get("chunk_id") or chunk.get("id") or f"{chunk_page_id}#{chunk_index}")
            data[chunk_id] = {
                "chunk_id": chunk_id,
                "page_id": chunk_page_id,
                "chunk_index": chunk_index,
                "chunk_text": str(chunk.get("chunk_text", chunk.get("text", ""))),
                "heading_path": str(chunk.get("heading_path", chunk.get("headingPath", ""))),
                "embedding": chunk.get("embedding", []),
            }
        save_vectors(Path(projectPath), data)

    def vector_search_chunks(self, projectPath: str, queryEmbedding: list[float], limit: int = 10, topK: int | None = None, **_: Any) -> list[dict[str, Any]]:
        data = load_vectors(Path(projectPath))
        scored = []
        for chunk in data.values():
            embedding = chunk.get("embedding")
            if not isinstance(embedding, list):
                continue
            score = cosine(queryEmbedding, embedding)
            scored.append({
                "chunk_id": chunk.get("chunk_id"),
                "page_id": chunk.get("page_id"),
                "chunk_index": chunk.get("chunk_index", 0),
                "chunk_text": chunk.get("chunk_text", ""),
                "heading_path": chunk.get("heading_path", ""),
                "score": score,
            })
        return sorted(scored, key=lambda item: item["score"], reverse=True)[:topK or limit]

    def vector_delete_page(self, projectPath: str, pagePath: str | None = None, pageId: str | None = None) -> None:
        target = pageId or pagePath
        data = load_vectors(Path(projectPath))
        data = {
            key: chunk for key, chunk in data.items()
            if chunk.get("page_id") != target and chunk.get("pagePath") != target and chunk.get("path") != target
        }
        save_vectors(Path(projectPath), data)

    def vector_count_chunks(self, projectPath: str) -> int:
        return len(load_vectors(Path(projectPath)))

    def claude_cli_detect(self) -> dict[str, Any]:
        path = shutil.which("claude")
        if not path:
            return {"installed": False, "version": None, "path": None, "error": "`claude` not found on PATH"}
        try:
            output = subprocess.run([path, "--version"], capture_output=True, text=True, timeout=3, check=False)
            version = (output.stdout or output.stderr).strip() or None
        except Exception as exc:
            return {"installed": False, "version": None, "path": path, "error": str(exc)}
        return {"installed": True, "version": version, "path": path, "error": None}

    def claude_cli_spawn(self, streamId: str, **_: Any) -> None:
        self.context.events.emit(f"claude-cli://{streamId}", {"type": "error", "error": "Claude CLI streaming is not implemented in the Python bridge yet."})

    def set_proxy_env(self, config: dict[str, Any]) -> None:
        if not config or not config.get("enabled"):
            return
        url = config.get("url") or config.get("http") or config.get("https")
        if url:
            os.environ["HTTP_PROXY"] = str(url)
            os.environ["HTTPS_PROXY"] = str(url)
        no_proxy = config.get("noProxy")
        if no_proxy:
            os.environ["NO_PROXY"] = str(no_proxy)

    def open_dialog(self, directory: bool = False, multiple: bool = False, **_: Any) -> str | list[str] | None:
        try:
            import tkinter as tk
            from tkinter import filedialog

            root = tk.Tk()
            root.withdraw()
            if directory:
                value = filedialog.askdirectory()
            elif multiple:
                value = list(filedialog.askopenfilenames())
            else:
                value = filedialog.askopenfilename()
            root.destroy()
            if multiple:
                return [normalize_path(Path(item)) for item in value]
            return normalize_path(Path(value)) if value else None
        except Exception:
            return None

    def open_url(self, url: str) -> None:
        webbrowser.open(url)

    def app_store_get(self, storeName: str, key: str) -> Any:
        return self.context.app_store.get(storeName, key)

    def app_store_set(self, storeName: str, key: str, value: Any) -> None:
        self.context.app_store.set(storeName, key, value)

    def app_store_delete(self, storeName: str, key: str) -> None:
        self.context.app_store.delete(storeName, key)

    def app_store_save(self, storeName: str) -> None:
        self.context.app_store.save(storeName)


class PollingWatcher:
    def __init__(self, project_id: str, project_path: Path, events: EventHub) -> None:
        self.project_id = project_id
        self.project_path = project_path
        self.events = events
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True)

    def start(self) -> None:
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        self._thread.join(timeout=1)

    def _run(self) -> None:
        while not self._stop.wait(2.0):
            try:
                rescan_project(self.project_id, self.project_path, self.events)
            except Exception:
                continue


def build_tree(root: Path, depth: int = 0, max_depth: int = 30) -> list[dict[str, Any]]:
    if depth >= max_depth:
        return []
    entries = [
        item for item in root.iterdir()
        if not item.name.startswith(".")
    ]
    entries.sort(key=lambda item: (not item.is_dir(), item.name.lower()))
    nodes = []
    for item in entries:
        node: dict[str, Any] = {
            "name": item.name,
            "path": normalize_path(item),
            "is_dir": item.is_dir(),
        }
        if item.is_dir():
            children = build_tree(item, depth + 1, max_depth)
            if children:
                node["children"] = children
        nodes.append(node)
    return nodes


def create_project_structure(root: Path, name: str) -> None:
    for rel in (
        "raw/sources",
        "raw/assets",
        "wiki/entities",
        "wiki/concepts",
        "wiki/sources",
        "wiki/queries",
        "wiki/comparisons",
        "wiki/synthesis",
        ".obsidian",
        ".llm-wiki",
    ):
        (root / rel).mkdir(parents=True, exist_ok=True)
    today = time.strftime("%Y-%m-%d")
    write_if_missing(root / "schema.md", DEFAULT_SCHEMA)
    write_if_missing(root / "purpose.md", DEFAULT_PURPOSE)
    write_if_missing(root / "wiki/index.md", DEFAULT_INDEX)
    write_if_missing(root / "wiki/log.md", f"# Research Log\n\n## {today}\n\n- Project created\n")
    write_if_missing(root / "wiki/overview.md", DEFAULT_OVERVIEW)
    write_if_missing(root / ".obsidian/app.json", OBSIDIAN_APP)
    write_if_missing(root / ".obsidian/appearance.json", OBSIDIAN_APPEARANCE)
    write_if_missing(root / ".obsidian/core-plugins.json", OBSIDIAN_CORE_PLUGINS)
    write_if_missing(root / ".llm-wiki/project.json", json.dumps({"id": str(uuid.uuid4()), "createdAt": now_ms(), "name": name}, indent=2))


def validate_project(root: Path) -> None:
    if not root.exists():
        raise FileNotFoundError(f"Path does not exist: '{root}'")
    if not root.is_dir():
        raise NotADirectoryError(f"Path is not a directory: '{root}'")
    if not (root / "schema.md").exists():
        raise ValueError(f"Not a valid wiki project (missing schema.md): '{root}'")
    if not (root / "wiki").is_dir():
        raise ValueError(f"Not a valid wiki project (missing wiki/ directory): '{root}'")


def scan_project(root: Path) -> dict[str, dict[str, Any]]:
    files: dict[str, dict[str, Any]] = {}
    if not root.exists():
        return files
    for path in root.rglob("*"):
        if not path.is_file():
            continue
        rel = normalize_path(path.relative_to(root))
        if should_ignore_rel(rel):
            continue
        stat = path.stat()
        files[rel] = {
            "size": stat.st_size,
            "mtimeMs": int(stat.st_mtime * 1000),
            "hash": md5_file(path),
        }
    return files


def rescan_project(project_id: str, project_path: Path, events: EventHub | None = None) -> dict[str, Any]:
    project_path = project_path.resolve()
    old_snapshot = load_snapshot(project_path)
    new_files = scan_project(project_path)
    old_files = old_snapshot.get("files", {})
    tasks = []
    for rel, meta in new_files.items():
        old = old_files.get(rel)
        if old is None:
            tasks.append(make_task(project_id, rel, "created", None, meta))
        elif old.get("hash") != meta.get("hash") or old.get("mtimeMs") != meta.get("mtimeMs"):
            tasks.append(make_task(project_id, rel, "modified", old, meta))
    for rel, old in old_files.items():
        if rel not in new_files:
            tasks.append(make_task(project_id, rel, "deleted", old, None))
    save_snapshot(project_path, {"version": 1, "files": new_files})
    queue_data = merge_queue(load_queue(project_path), tasks)
    save_queue(project_path, queue_data)
    if events and tasks:
        emit_queue(events, project_id, queue_data)
        events.emit(EVENT_CHANGED, {"projectId": project_id, "tasks": tasks})
    return queue_data


def make_task(project_id: str, path: str, kind: str, before: dict[str, Any] | None, after: dict[str, Any] | None) -> dict[str, Any]:
    created = now_ms()
    return {
        "id": f"{kind}:{path}:{created}",
        "projectId": project_id,
        "path": path,
        "kind": kind,
        "status": "pending",
        "hashBefore": before.get("hash") if before else None,
        "hashAfter": after.get("hash") if after else None,
        "size": after.get("size") if after else before.get("size") if before else None,
        "mtimeMs": after.get("mtimeMs") if after else before.get("mtimeMs") if before else None,
        "createdAt": created,
        "updatedAt": created,
        "retryCount": 0,
        "error": None,
        "needsRerun": False,
    }


def merge_queue(queue_data: dict[str, Any], tasks: list[dict[str, Any]]) -> dict[str, Any]:
    existing = {
        (task.get("projectId"), task.get("path"), task.get("kind")): task
        for task in queue_data.get("tasks", [])
    }
    for task in tasks:
        existing[(task["projectId"], task["path"], task["kind"])] = task
    return {"version": 1, "tasks": list(existing.values())}


def emit_queue(events: EventHub, project_id: str, queue_data: dict[str, Any]) -> None:
    events.emit(EVENT_QUEUE_UPDATED, {"projectId": project_id, "tasks": queue_data.get("tasks", [])})


def load_snapshot(project: Path) -> dict[str, Any]:
    return load_json(project / SNAPSHOT_FILE, {"version": 1, "files": {}})


def save_snapshot(project: Path, data: dict[str, Any]) -> None:
    save_json(project / SNAPSHOT_FILE, data)


def load_queue(project: Path) -> dict[str, Any]:
    return load_json(project / QUEUE_FILE, {"version": 1, "tasks": []})


def save_queue(project: Path, data: dict[str, Any]) -> None:
    save_json(project / QUEUE_FILE, data)


def load_vectors(project: Path) -> dict[str, Any]:
    return load_json(project / ".llm-wiki/vector-chunks.json", {})


def save_vectors(project: Path, data: dict[str, Any]) -> None:
    save_json(project / ".llm-wiki/vector-chunks.json", data)


def load_json(path: Path, default: Any) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return default


def save_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)


def cache_path_for(original: Path) -> Path:
    return original.parent / ".cache" / f"{original.name}.txt"


def write_if_missing(path: Path, contents: str) -> None:
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(contents, encoding="utf-8")


def normalize_path(path: Path) -> str:
    return str(path).replace("\\", "/")


def now_ms() -> int:
    return int(time.time() * 1000)


def should_ignore_rel(rel: str) -> bool:
    parts = rel.split("/")
    return any(part.startswith(".") for part in parts)


def md5_file(path: Path) -> str:
    digest = hashlib.md5()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def cosine(a: list[float], b: list[float]) -> float:
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    norm_a = sum(x * x for x in a) ** 0.5
    norm_b = sum(y * y for y in b) ** 0.5
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return dot / (norm_a * norm_b)


DEFAULT_SCHEMA = """# Wiki Schema

## Page Types

| Type | Directory | Purpose |
|------|-----------|---------|
| entity | wiki/entities/ | Named things |
| concept | wiki/concepts/ | Ideas, techniques, phenomena |
| source | wiki/sources/ | Papers, articles, talks, blog posts |
| query | wiki/queries/ | Open questions under investigation |
| comparison | wiki/comparisons/ | Side-by-side analysis |
| synthesis | wiki/synthesis/ | Cross-cutting summaries |

Use YAML frontmatter and `[[wikilinks]]` for cross references.
"""

DEFAULT_PURPOSE = """# Project Purpose

## Goal

<!-- What are you trying to understand or build? -->

## Key Questions

1.
2.
3.
"""

DEFAULT_INDEX = """# Wiki Index

## Entities

## Concepts

## Sources

## Queries

## Comparisons

## Synthesis
"""

DEFAULT_OVERVIEW = """---
type: overview
title: Project Overview
tags: []
related: []
---

# Overview

<!-- Provide a high-level summary of what this wiki covers. -->
"""

OBSIDIAN_APP = """{
  "attachmentFolderPath": "raw/assets",
  "userIgnoreFilters": [".cache", ".llm-wiki", ".superpowers"],
  "useMarkdownLinks": false,
  "newLinkFormat": "shortest",
  "showUnsupportedFiles": false
}"""

OBSIDIAN_APPEARANCE = """{
  "baseFontSize": 16,
  "theme": "obsidian"
}"""

OBSIDIAN_CORE_PLUGINS = """{
  "file-explorer": true,
  "global-search": true,
  "graph": true,
  "backlink": true,
  "tag-pane": true,
  "page-preview": true,
  "outgoing-link": true,
  "starred": true
}"""
