from __future__ import annotations

from pathlib import Path

from app.services.files import FileService
from app.services.graph import GraphService
from app.services.projects import ProjectService
from app.services.search import SearchService
from app.storage import SQLiteStore


def make_project(tmp_path: Path):
    store = SQLiteStore(tmp_path / "state.sqlite3")
    projects = ProjectService(tmp_path / "projects", store)
    files = FileService()
    project = projects.create_project("Graph Search")
    return project, files, store


def test_settings_tasks_and_search_meta_persist(tmp_path: Path):
    store = SQLiteStore(tmp_path / "state.sqlite3")
    projects = ProjectService(tmp_path / "projects", store)
    project = projects.create_project("Persistent State")

    store.set_setting("language", "zh")
    store.upsert_task("t1", project.id, "ingest", "queued", "Import source")

    assert store.get_setting("language") == "zh"
    assert store.list_tasks(project.id)[0]["label"] == "Import source"


def test_search_indexes_wiki_and_sources(tmp_path: Path):
    project, files, store = make_project(tmp_path)
    files.write_text(project, "wiki/alpha.md", "# Alpha\n\nNeural retrieval and graph search.")
    files.write_text(project, "raw/sources/source.txt", "A source about retrieval systems.")

    results = SearchService(files, store).search(project, "retrieval")

    assert [result.path for result in results] == ["raw/sources/source.txt", "wiki/alpha.md"]
    assert store.get_search_meta(project.id)["document_count"] >= 4


def test_graph_builds_wikilink_and_shared_source_edges(tmp_path: Path):
    project, files, _ = make_project(tmp_path)
    files.write_text(
        project,
        "wiki/alpha.md",
        "---\ntitle: Alpha\ntype: concept\nsources:\n  - raw/sources/a.txt\n---\n# Alpha\n\nSee [[Beta]].",
    )
    files.write_text(
        project,
        "wiki/beta.md",
        "---\ntitle: Beta\ntype: concept\nsources: [raw/sources/a.txt]\n---\n# Beta\n\nBack to [[Alpha]].",
    )

    nodes, edges = GraphService(files).build(project)

    assert {node.label for node in nodes} >= {"Alpha", "Beta"}
    reasons = {edge.reason for edge in edges}
    assert "wikilink" in reasons
    assert "shared source" in reasons
