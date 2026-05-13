from __future__ import annotations

import shutil
from pathlib import Path

from app.models import FileNode, WikiProject


TEXT_EXTENSIONS = {
    ".md",
    ".markdown",
    ".txt",
    ".json",
    ".yaml",
    ".yml",
    ".csv",
    ".html",
    ".htm",
    ".py",
    ".js",
    ".ts",
    ".tsx",
    ".css",
}


class FileService:
    def list_tree(self, project: WikiProject, rel_path: str = "") -> list[FileNode]:
        root = self.resolve(project, rel_path)
        if not root.exists():
            return []
        if root.is_file():
            return [self._node(project, root)]
        children = [self._node(project, child) for child in sorted(root.iterdir(), key=self._sort_key)]
        return children

    def read_text(self, project: WikiProject, rel_path: str) -> str:
        path = self.resolve(project, rel_path)
        if not path.is_file():
            raise FileNotFoundError(rel_path)
        return path.read_text(encoding="utf-8")

    def write_text(self, project: WikiProject, rel_path: str, contents: str) -> None:
        path = self.resolve(project, rel_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(contents, encoding="utf-8")

    def delete(self, project: WikiProject, rel_path: str) -> None:
        path = self.resolve(project, rel_path)
        if path.is_dir():
            shutil.rmtree(path)
        elif path.exists():
            path.unlink()

    def import_source(self, project: WikiProject, source_path: str | Path) -> str:
        source = Path(source_path).expanduser().resolve()
        if not source.exists() or not source.is_file():
            raise FileNotFoundError(source)
        target_dir = self.resolve(project, "raw/sources")
        target_dir.mkdir(parents=True, exist_ok=True)
        target = self._unique_path(target_dir / source.name)
        shutil.copy2(source, target)
        return self.rel(project, target)

    def iter_documents(self, project: WikiProject, rel_roots: tuple[str, ...] = ("wiki", "raw/sources")):
        for rel_root in rel_roots:
            root = self.resolve(project, rel_root)
            if not root.exists():
                continue
            for path in root.rglob("*"):
                if path.is_file() and self.is_text(path):
                    yield self.rel(project, path), path

    def is_text(self, path: Path) -> bool:
        return path.suffix.lower() in TEXT_EXTENSIONS

    def resolve(self, project: WikiProject, rel_path: str | Path) -> Path:
        raw = Path(rel_path)
        if raw.is_absolute():
            candidate = raw.resolve()
        else:
            candidate = (project.path / raw).resolve()
        project_root = project.path.resolve()
        if candidate != project_root and project_root not in candidate.parents:
            raise ValueError(f"Path escapes project root: {rel_path}")
        return candidate

    def rel(self, project: WikiProject, path: Path) -> str:
        return path.resolve().relative_to(project.path.resolve()).as_posix()

    def _node(self, project: WikiProject, path: Path) -> FileNode:
        children: list[FileNode] = []
        if path.is_dir():
            children = [
                self._node(project, child)
                for child in sorted(path.iterdir(), key=self._sort_key)
            ]
        return FileNode(
            name=path.name,
            path=self.rel(project, path),
            is_dir=path.is_dir(),
            children=children,
        )

    def _sort_key(self, path: Path) -> tuple[int, str]:
        return (0 if path.is_dir() else 1, path.name.lower())

    def _unique_path(self, path: Path) -> Path:
        if not path.exists():
            return path
        stem = path.stem
        suffix = path.suffix
        for index in range(2, 1000):
            candidate = path.with_name(f"{stem}-{index}{suffix}")
            if not candidate.exists():
                return candidate
        raise RuntimeError(f"Could not allocate unique path for {path.name}")

