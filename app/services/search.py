from __future__ import annotations

import re

from app.models import SearchResult, WikiProject
from app.services.files import FileService
from app.services.markdown import title_from_markdown
from app.storage import SQLiteStore


TOKEN_RE = re.compile(r"[\w\u4e00-\u9fff]+", re.UNICODE)


class SearchService:
    def __init__(self, files: FileService, store: SQLiteStore):
        self.files = files
        self.store = store

    def search(self, project: WikiProject, query: str, limit: int = 30) -> list[SearchResult]:
        terms = tokenize(query)
        documents = list(self.files.iter_documents(project))
        self.store.update_search_meta(project.id, len(documents))
        if not terms:
            return []

        results: list[SearchResult] = []
        for rel_path, path in documents:
            try:
                content = path.read_text(encoding="utf-8")
            except UnicodeDecodeError:
                continue
            title = title_from_markdown(rel_path, content)
            haystack = f"{title}\n{content}".lower()
            score = 0.0
            for term in terms:
                count = haystack.count(term)
                if count:
                    score += count
                    if term in title.lower():
                        score += 10
            if score <= 0:
                continue
            results.append(
                SearchResult(
                    path=rel_path,
                    title=title,
                    excerpt=excerpt_for(content, terms),
                    score=score,
                )
            )
        return sorted(results, key=lambda item: (-item.score, item.path))[:limit]


def tokenize(query: str) -> list[str]:
    return [token.lower() for token in TOKEN_RE.findall(query)]


def excerpt_for(content: str, terms: list[str], size: int = 180) -> str:
    lower = content.lower()
    first = min((lower.find(term) for term in terms if term in lower), default=0)
    start = max(0, first - size // 3)
    excerpt = content[start : start + size].replace("\n", " ").strip()
    if start > 0:
        excerpt = "..." + excerpt
    if start + size < len(content):
        excerpt += "..."
    return excerpt

