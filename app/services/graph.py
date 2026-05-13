from __future__ import annotations

from collections import defaultdict
from pathlib import Path

from app.models import GraphEdge, GraphNode, WikiProject
from app.services.files import FileService
from app.services.markdown import split_frontmatter, title_from_markdown, wikilinks


class GraphService:
    def __init__(self, files: FileService):
        self.files = files

    def build(self, project: WikiProject) -> tuple[list[GraphNode], list[GraphEdge]]:
        pages = self._load_pages(project)
        nodes = [
            GraphNode(id=page_id, label=page["title"], path=page["path"], kind=page["kind"])
            for page_id, page in pages.items()
        ]
        edges: dict[tuple[str, str, str], GraphEdge] = {}
        title_index = self._title_index(pages)

        for source_id, page in pages.items():
            for target_title in page["links"]:
                target_id = title_index.get(normalize_title(target_title))
                if target_id and target_id != source_id:
                    self._add_edge(edges, source_id, target_id, 3.0, "wikilink")

        by_source: dict[str, list[str]] = defaultdict(list)
        for page_id, page in pages.items():
            for source in page["sources"]:
                by_source[source].append(page_id)
        for page_ids in by_source.values():
            for index, source_id in enumerate(page_ids):
                for target_id in page_ids[index + 1 :]:
                    if source_id != target_id:
                        self._add_edge(edges, source_id, target_id, 4.0, "shared source")

        return nodes, sorted(edges.values(), key=lambda edge: (edge.source, edge.target, edge.reason))

    def _load_pages(self, project: WikiProject) -> dict[str, dict[str, object]]:
        pages: dict[str, dict[str, object]] = {}
        wiki_root = self.files.resolve(project, "wiki")
        if not wiki_root.exists():
            return pages
        for path in sorted(wiki_root.rglob("*.md")):
            rel = self.files.rel(project, path)
            content = path.read_text(encoding="utf-8")
            frontmatter, body = split_frontmatter(content)
            title = str(frontmatter.get("title") or title_from_markdown(rel, body))
            kind = str(frontmatter.get("type") or page_kind(path))
            sources = frontmatter.get("sources") or []
            if isinstance(sources, str):
                sources = [sources]
            page_id = rel
            pages[page_id] = {
                "path": rel,
                "title": title,
                "kind": kind,
                "links": wikilinks(body),
                "sources": [str(source) for source in sources],
            }
        return pages

    def _title_index(self, pages: dict[str, dict[str, object]]) -> dict[str, str]:
        index: dict[str, str] = {}
        for page_id, page in pages.items():
            rel_title = Path(page_id).stem
            index.setdefault(normalize_title(rel_title), page_id)
            index.setdefault(normalize_title(str(page["title"])), page_id)
        return index

    def _add_edge(
        self,
        edges: dict[tuple[str, str, str], GraphEdge],
        source: str,
        target: str,
        weight: float,
        reason: str,
    ) -> None:
        left, right = sorted((source, target))
        key = (left, right, reason)
        existing = edges.get(key)
        if existing is None:
            edges[key] = GraphEdge(source=left, target=right, weight=weight, reason=reason)
        else:
            edges[key] = GraphEdge(
                source=left,
                target=right,
                weight=existing.weight + weight,
                reason=reason,
            )


def normalize_title(value: str) -> str:
    return value.strip().lower().replace(" ", "-")


def page_kind(path: Path) -> str:
    parent = path.parent.name
    if parent and parent != "wiki":
        return parent
    return "page"

