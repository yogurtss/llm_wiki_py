from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class WikiProject:
    id: str
    name: str
    path: Path

    def to_dict(self) -> dict[str, str]:
        return {"id": self.id, "name": self.name, "path": str(self.path)}


@dataclass
class FileNode:
    name: str
    path: str
    is_dir: bool
    children: list["FileNode"] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        data: dict[str, Any] = {
            "name": self.name,
            "path": self.path,
            "is_dir": self.is_dir,
        }
        if self.children:
            data["children"] = [child.to_dict() for child in self.children]
        return data


@dataclass(frozen=True)
class SearchResult:
    path: str
    title: str
    excerpt: str
    score: float


@dataclass(frozen=True)
class GraphNode:
    id: str
    label: str
    path: str
    kind: str


@dataclass(frozen=True)
class GraphEdge:
    source: str
    target: str
    weight: float
    reason: str

