from __future__ import annotations

import re
from typing import Any


WIKILINK_RE = re.compile(r"\[\[([^\]|#]+)(?:#[^\]|]+)?(?:\|[^\]]+)?\]\]")


def split_frontmatter(markdown: str) -> tuple[dict[str, Any], str]:
    if not markdown.startswith("---\n"):
        return {}, markdown
    end = markdown.find("\n---", 4)
    if end == -1:
        return {}, markdown
    raw = markdown[4:end].strip()
    body = markdown[end + 4 :].lstrip("\n")
    return parse_simple_yaml(raw), body


def parse_simple_yaml(raw: str) -> dict[str, Any]:
    data: dict[str, Any] = {}
    current_key: str | None = None
    for line in raw.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if stripped.startswith("- ") and current_key:
            data.setdefault(current_key, []).append(_clean_scalar(stripped[2:]))
            continue
        if ":" not in line:
            continue
        key, value = line.split(":", 1)
        key = key.strip()
        value = value.strip()
        current_key = key
        if value == "":
            data[key] = []
        elif value.startswith("[") and value.endswith("]"):
            inside = value[1:-1].strip()
            data[key] = [] if not inside else [_clean_scalar(part) for part in inside.split(",")]
        else:
            data[key] = _clean_scalar(value)
    return data


def wikilinks(markdown: str) -> list[str]:
    return [match.group(1).strip() for match in WIKILINK_RE.finditer(markdown)]


def title_from_markdown(path: str, markdown: str) -> str:
    for line in markdown.splitlines():
        stripped = line.strip()
        if stripped.startswith("# "):
            return stripped[2:].strip()
    return path.rsplit("/", 1)[-1].removesuffix(".md")


def _clean_scalar(value: str) -> str:
    value = value.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {'"', "'"}:
        return value[1:-1]
    return value

