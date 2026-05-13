from __future__ import annotations

from pathlib import Path

import pytest

from app.services.files import FileService
from app.services.projects import ProjectService
from app.storage import SQLiteStore


@pytest.fixture
def services(tmp_path: Path):
    store = SQLiteStore(tmp_path / "state.sqlite3")
    projects = ProjectService(tmp_path / "projects", store)
    files = FileService()
    return projects, files, store


def test_create_project_writes_compatible_structure(services):
    projects, _, store = services

    project = projects.create_project("Research Notes")

    assert project.path.exists()
    assert (project.path / "wiki").is_dir()
    assert (project.path / "raw/sources").is_dir()
    assert (project.path / ".llm-wiki/project.json").is_file()
    assert (project.path / "schema.md").is_file()
    assert (project.path / "purpose.md").is_file()
    assert (project.path / "wiki/index.md").is_file()
    assert store.list_recent_projects()[0].id == project.id


def test_open_existing_project_creates_identity_and_registers_it(services, tmp_path: Path):
    projects, _, store = services
    existing = tmp_path / "external-wiki"
    existing.mkdir()

    project = projects.open_project(existing)

    assert project.name == "external-wiki"
    assert (existing / ".llm-wiki/project.json").is_file()
    assert (existing / "wiki").is_dir()
    assert store.list_recent_projects()[0].path == existing


def test_file_service_reads_writes_scans_and_blocks_path_escape(services):
    projects, files, _ = services
    project = projects.create_project("Safe Paths")

    files.write_text(project, "wiki/page.md", "# Page\n\nHello")

    assert files.read_text(project, "wiki/page.md") == "# Page\n\nHello"
    tree = files.list_tree(project, "wiki")
    assert [node.name for node in tree] == ["index.md", "log.md", "page.md"]

    with pytest.raises(ValueError):
        files.read_text(project, "../outside.md")


def test_import_source_copies_file_into_project(services, tmp_path: Path):
    projects, files, _ = services
    project = projects.create_project("Sources")
    source = tmp_path / "paper.txt"
    source.write_text("source body", encoding="utf-8")

    rel = files.import_source(project, source)

    assert rel == "raw/sources/paper.txt"
    assert files.read_text(project, rel) == "source body"

