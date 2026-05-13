from __future__ import annotations

from pathlib import Path
from urllib.request import Request, urlopen
import json

from app.config import AppConfig
from app.pybridge.commands import BridgeContext, CommandDispatcher
from app.pybridge.events import EventHub
from app.pybridge.server import BridgeServer
from app.pybridge.store import JsonAppStore


def dispatcher(tmp_path: Path) -> CommandDispatcher:
    config = AppConfig(
        root=tmp_path / "state",
        workspace=tmp_path / "projects",
        database=tmp_path / "state" / "llmwiki.sqlite3",
    )
    return CommandDispatcher(
        BridgeContext(
            config=config,
            events=EventHub(),
            app_store=JsonAppStore(config.root),
        )
    )


def test_project_file_commands_create_original_project_shape(tmp_path: Path) -> None:
    commands = dispatcher(tmp_path)

    project = commands.invoke("create_project", {"name": "Demo Wiki", "path": str(tmp_path)})

    root = Path(project["path"])
    assert project == {"name": "Demo Wiki", "path": str(root).replace("\\", "/")}
    assert (root / "schema.md").is_file()
    assert (root / "purpose.md").is_file()
    assert (root / "wiki/index.md").is_file()
    assert (root / "wiki/log.md").is_file()
    assert (root / "wiki/overview.md").is_file()
    assert (root / ".obsidian/app.json").is_file()
    assert (root / ".llm-wiki/project.json").is_file()

    note = root / "wiki/entities/python.md"
    commands.invoke("write_file", {"path": str(note), "contents": "# Python\n"})
    assert commands.invoke("read_file", {"path": str(note)}) == "# Python\n"

    tree = commands.invoke("list_directory", {"path": str(root)})
    names = [node["name"] for node in tree]
    assert "raw" in names
    assert "wiki" in names
    assert "schema.md" in names

    opened = commands.invoke("open_project", {"path": str(root)})
    assert opened["name"] == "Demo Wiki"
    assert opened["path"] == str(root).replace("\\", "/")


def test_app_store_matches_tauri_plugin_store_key_value_shape(tmp_path: Path) -> None:
    commands = dispatcher(tmp_path)

    commands.invoke("app_store_set", {"storeName": "app-state.json", "key": "language", "value": "zh"})

    assert commands.invoke("app_store_get", {"storeName": "app-state.json", "key": "language"}) == "zh"
    commands.invoke("app_store_delete", {"storeName": "app-state.json", "key": "language"})
    assert commands.invoke("app_store_get", {"storeName": "app-state.json", "key": "language"}) is None


def test_file_sync_rescan_persists_queue_and_snapshot(tmp_path: Path) -> None:
    commands = dispatcher(tmp_path)
    project = commands.invoke("create_project", {"name": "Sync Wiki", "path": str(tmp_path)})
    root = Path(project["path"])

    first = commands.invoke("rescan_project_files", {"projectId": "proj-1", "projectPath": str(root)})
    assert first["version"] == 1
    assert first["tasks"]

    source = root / "raw/sources/note.md"
    source.write_text("hello", encoding="utf-8")
    second = commands.invoke("rescan_project_files", {"projectId": "proj-1", "projectPath": str(root)})

    assert any(task["path"] == "raw/sources/note.md" and task["kind"] == "created" for task in second["tasks"])
    persisted = commands.invoke("get_file_change_queue", {"projectPath": str(root)})
    assert persisted == second


def test_vector_chunk_commands_round_trip(tmp_path: Path) -> None:
    commands = dispatcher(tmp_path)
    project = commands.invoke("create_project", {"name": "Vector Wiki", "path": str(tmp_path)})

    commands.invoke(
        "vector_upsert_chunks",
        {
            "projectPath": project["path"],
            "pageId": "wiki/a.md",
            "chunks": [
                {"chunk_index": 0, "chunk_text": "alpha", "heading_path": "", "embedding": [1.0, 0.0]},
            ],
        },
    )
    commands.invoke(
        "vector_upsert_chunks",
        {
            "projectPath": project["path"],
            "pageId": "wiki/b.md",
            "chunks": [
                {"chunk_index": 0, "chunk_text": "beta", "heading_path": "", "embedding": [0.0, 1.0]},
            ],
        },
    )

    assert commands.invoke("vector_count_chunks", {"projectPath": project["path"]}) == 2
    results = commands.invoke(
        "vector_search_chunks",
        {"projectPath": project["path"], "queryEmbedding": [1.0, 0.0], "topK": 1},
    )
    assert results[0]["page_id"] == "wiki/a.md"

    commands.invoke("vector_delete_page", {"projectPath": project["path"], "pageId": "wiki/a.md"})
    assert commands.invoke("vector_count_chunks", {"projectPath": project["path"]}) == 1


def test_bridge_server_exposes_invoke_contract(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("LLMWIKI_HOME", str(tmp_path / "state"))
    server = BridgeServer(host="127.0.0.1", port=0)
    url = server.start()
    try:
        request = Request(
            f"{url}/api/invoke",
            data=json.dumps({
                "command": "create_project",
                "args": {"name": "HTTP Wiki", "path": str(tmp_path)},
            }).encode("utf-8"),
            headers={"content-type": "application/json"},
            method="POST",
        )
        with urlopen(request, timeout=3) as response:
            payload = json.loads(response.read().decode("utf-8"))
    finally:
        server.stop()

    assert payload["ok"] is True
    assert payload["result"]["name"] == "HTTP Wiki"
    assert Path(payload["result"]["path"], "wiki/index.md").is_file()
