from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from nicegui import ui

from app.config import load_config
from app.models import FileNode, SearchResult, WikiProject
from app.services.chat import ChatStore
from app.services.files import FileService
from app.services.graph import GraphService
from app.services.markdown import wikilinks
from app.services.projects import ProjectService
from app.services.search import SearchService
from app.services.settings import DEFAULT_LLM_SETTINGS, SettingsStore
from app.storage import SQLiteStore


config = load_config()
store = SQLiteStore(config.database)
projects = ProjectService(config.workspace, store)
files = FileService()
settings = SettingsStore(store)
chat_store = ChatStore(store)
search_service = SearchService(files, store)
graph_service = GraphService(files)


@dataclass
class PageState:
    project: WikiProject | None = None
    active_view: str = "wiki"
    sidebar_tab: str = "knowledge"
    selected_path: str | None = None
    draft: str = ""
    preview_editing: bool = False
    active_conversation_id: str | None = None
    search_query: str = ""
    search_results: list[SearchResult] = field(default_factory=list)
    status: str = ""


state = PageState()


def set_status(message: str) -> None:
    state.status = message
    ui.notify(message)


def select_project(project: WikiProject) -> None:
    state.project = project
    state.active_view = "wiki"
    state.sidebar_tab = "knowledge"
    state.selected_path = first_existing_path(project, ("schema.md", "purpose.md", "wiki/index.md"))
    state.draft = files.read_text(project, state.selected_path) if state.selected_path else ""
    conversations = chat_store.list_conversations(project.id)
    state.active_conversation_id = str(conversations[0]["id"]) if conversations else None
    app_view.refresh()


def select_file(path: str) -> None:
    if state.project is None:
        return
    normalized_path = normalize_project_rel_path(path)
    state.selected_path = normalized_path
    state.preview_editing = False
    try:
        state.draft = files.read_text(state.project, normalized_path)
    except Exception as exc:
        fallback = recover_common_file_path(state.project, normalized_path)
        if fallback:
            state.selected_path = fallback
            try:
                state.draft = files.read_text(state.project, fallback)
                set_status(f"Opened {fallback}")
            except Exception as fallback_exc:
                state.draft = f"Error loading file: {fallback_exc}"
                set_status(f"Unable to read {fallback}: {fallback_exc}")
        else:
            state.draft = f"Error loading file: {exc}"
            set_status(f"Unable to read {normalized_path}: {exc}")
    app_view.refresh()


def first_existing_path(project: WikiProject, candidates: tuple[str, ...]) -> str | None:
    for candidate in candidates:
        try:
            if files.resolve(project, candidate).is_file():
                return candidate
        except ValueError:
            continue
    return None


def normalize_project_rel_path(path: str) -> str:
    path = path.replace("\\", "/").strip()
    if state.project is not None:
        project_prefix = str(state.project.path).replace("\\", "/").rstrip("/") + "/"
        if path.startswith(project_prefix):
            path = path[len(project_prefix):]
    while path.startswith("./"):
        path = path[2:]
    return path


def recover_common_file_path(project: WikiProject, path: str) -> str | None:
    # A common UI slip during source browsing is to carry the `raw/sources`
    # prefix when the selected file actually lives at the project root.
    if path.startswith("raw/sources/"):
        root_candidate = path.removeprefix("raw/sources/")
        if "/" not in root_candidate and files.resolve(project, root_candidate).is_file():
            return root_candidate
    basename = Path(path).name
    for candidate in (basename, f"wiki/{basename}", f"raw/sources/{basename}"):
        if files.resolve(project, candidate).is_file():
            return candidate
    return None


def jump_to_wikilink(target: str) -> None:
    if state.project is None:
        return
    match = find_wiki_page(target)
    if match is None:
        set_status(f"Wiki page not found: {target}")
        return
    state.active_view = "wiki"
    select_file(match)


def find_wiki_page(target: str) -> str | None:
    if state.project is None:
        return None
    normalized = normalize_link_target(target)
    for rel_path, path in files.iter_documents(state.project, ("wiki",)):
        if not rel_path.endswith(".md"):
            continue
        candidates = {
            normalize_link_target(Path(rel_path).stem),
            normalize_link_target(rel_path.removeprefix("wiki/").removesuffix(".md")),
        }
        try:
            content = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            content = ""
        for line in content.splitlines():
            if line.startswith("title:"):
                candidates.add(normalize_link_target(line.split(":", 1)[1].strip().strip("\"'")))
                break
        if normalized in candidates:
            return rel_path
    return None


def normalize_link_target(value: str) -> str:
    return value.strip().lower().replace(" ", "-").replace("_", "-")


def save_selected_file() -> None:
    if state.project is None or state.selected_path is None:
        return
    files.write_text(state.project, state.selected_path, state.draft)
    state.preview_editing = False
    set_status(f"Saved {state.selected_path}")
    app_view.refresh()


def switch_view(view: str) -> None:
    state.active_view = view
    app_view.refresh()


@ui.page("/")
def index() -> None:
    ui.add_head_html(STYLE)
    app_view()


@ui.refreshable
def app_view() -> None:
    if state.project is None:
        render_welcome()
    else:
        render_project()


def render_welcome() -> None:
    with ui.column().classes("full-screen center-screen welcome-bg"):
        with ui.card().classes("welcome-card"):
            ui.label("LLM Wiki").classes("title")
            ui.label("Python monolith edition").classes("muted")
            ui.separator()
            name_input = ui.input("Project name", value="My Wiki").classes("full")
            open_input = ui.input("Open existing project path").classes("full")
            with ui.row().classes("gap"):
                ui.button(
                    "New Project",
                    icon="add",
                    on_click=lambda: create_project_from_input(name_input.value),
                ).props("unelevated")
                ui.button(
                    "Open Project",
                    icon="folder_open",
                    on_click=lambda: open_project_from_input(open_input.value),
                ).props("outline")
            recent = projects.recent_projects()
            if recent:
                ui.label("Recent projects").classes("section-title")
                for project in recent:
                    with ui.row().classes("recent-row"):
                        ui.button(
                            project.name,
                            on_click=lambda p=project: select_project(projects.open_project(p.path)),
                        ).props("flat align=left").classes("recent-button")
                        ui.label(str(project.path)).classes("recent-path")


def create_project_from_input(name: str) -> None:
    try:
        select_project(projects.create_project(name))
    except Exception as exc:
        set_status(f"Failed to create project: {exc}")


def open_project_from_input(path: str | None) -> None:
    if not path:
        set_status("Enter a project path first")
        return
    try:
        select_project(projects.open_project(path))
    except Exception as exc:
        set_status(f"Failed to open project: {exc}")


def render_project() -> None:
    assert state.project is not None
    with ui.column().classes("desktop-shell"):
        if state.active_view == "settings":
            with ui.row().classes("app-shell"):
                render_icon_sidebar()
                with ui.column().classes("main-pane settings-main"):
                    render_active_view()
            return
        with ui.row().classes("app-shell"):
            with ui.row().classes("nav-region"):
                render_icon_sidebar()
                with ui.column().classes("left-pane"):
                    render_left_sidebar()
            with ui.column().classes("main-pane"):
                render_active_view()
            with ui.column().classes("right-pane"):
                render_right_panel()


def render_window_bar() -> None:
    with ui.row().classes("window-bar"):
        with ui.row().classes("traffic-lights"):
            ui.label("").classes("traffic-dot red")
            ui.label("").classes("traffic-dot amber")
            ui.label("").classes("traffic-dot green")
        ui.label("LLM Wiki").classes("window-title")


def render_project_header() -> None:
    assert state.project is not None
    ui.label(state.project.name).classes("project-title")


def render_icon_sidebar() -> None:
    nav = [
        ("wiki", "description", "Wiki"),
        ("sources", "folder", "Sources"),
        ("search", "search", "Search"),
        ("graph", "hub", "Graph"),
        ("lint", "fact_check", "Lint"),
        ("review", "assignment", "Review"),
        ("settings", "settings", "Settings"),
    ]
    with ui.column().classes("icon-sidebar"):
        ui.label("▥").classes("logo")
        for view, icon, label in nav:
            props = "flat round"
            if state.active_view == view:
                props += " color=primary"
            ui.button(icon=icon, on_click=lambda v=view: switch_view(v)).props(props).tooltip(label)
        ui.space()
        ui.button(icon="language", on_click=lambda: set_status("Web clipper is planned for a later phase")).props("flat round").tooltip("Web")
        ui.button(icon="swap_horiz", on_click=switch_project).props("flat round").tooltip("Switch project")


def switch_project() -> None:
    state.project = None
    state.selected_path = None
    state.draft = ""
    state.preview_editing = False
    app_view.refresh()


def switch_sidebar_tab(tab: str) -> None:
    state.sidebar_tab = tab
    app_view.refresh()


def render_left_sidebar() -> None:
    assert state.project is not None
    with ui.row().classes("sidebar-tabs"):
        ui.button(
            "Knowledge",
            on_click=lambda: switch_sidebar_tab("knowledge"),
        ).props("flat dense").classes("sidebar-tab active-tab" if state.sidebar_tab == "knowledge" else "sidebar-tab")
        ui.button(
            "Files",
            on_click=lambda: switch_sidebar_tab("files"),
        ).props("flat dense").classes("sidebar-tab active-tab" if state.sidebar_tab == "files" else "sidebar-tab")
    with ui.column().classes("left-content"):
        render_project_header()
        if state.sidebar_tab == "files":
            render_file_tree()
        else:
            render_knowledge_tree()
    render_left_footer()


def render_file_tree() -> None:
    assert state.project is not None
    for node in files.list_tree(state.project):
        render_file_node(node)


def render_knowledge_tree() -> None:
    assert state.project is not None
    wiki_root = files.list_tree(state.project, "wiki")
    source_root = files.list_tree(state.project, "raw/sources")
    if not wiki_root:
        ui.label("No wiki pages yet").classes("muted small")
        return
    render_tree_section("Overview", [node for node in wiki_root if node.name in {"index.md", "overview.md"}])
    render_tree_section("Project Overview", [node for node in wiki_root if node.name == "log.md"])
    render_tree_section("Raw Sources", source_root)
    other_nodes = [node for node in wiki_root if node.name not in {"index.md", "overview.md", "log.md"}]
    render_tree_section("Entities", other_nodes)


def render_tree_section(label: str, nodes: list[FileNode]) -> None:
    if not nodes:
        return
    count = count_files(nodes)
    with ui.row().classes("tree-section-row"):
        ui.label(f"⌄ {label}").classes("tree-dir")
        ui.space()
        ui.label(str(count)).classes("tree-count")
    for node in nodes:
        render_file_node(node)


def count_files(nodes: list[FileNode]) -> int:
    total = 0
    for node in nodes:
        total += count_files(node.children) if node.is_dir else 1
    return total


def render_file_node(node: FileNode, depth: int = 0) -> None:
    padding = f"padding-left: {depth * 14}px"
    if node.is_dir:
        ui.label(f"▾ {node.name}").classes("tree-dir").style(padding)
        for child in node.children:
            render_file_node(child, depth + 1)
        return
    active = " selected-file" if node.path == state.selected_path else ""
    ui.button(
        node.name,
        icon="article",
        on_click=lambda p=node.path: select_file(p),
    ).props("flat dense align=left").classes(f"tree-file{active}").style(padding)


def render_activity_panel() -> None:
    assert state.project is not None
    ui.separator()
    ui.label("Activity").classes("section-title")
    tasks = store.list_tasks(state.project.id)
    if not tasks:
        ui.label("No active tasks").classes("muted small")
    for task in tasks[:6]:
        ui.label(f"{task['status']} · {task['label']}").classes("small")


def render_left_footer() -> None:
    assert state.project is not None
    with ui.column().classes("left-footer"):
        ui.button("Open project folder", icon="folder_open", on_click=lambda: set_status(str(state.project.path))).props("flat dense")
        with ui.expansion("Queue: 0/0", icon="sync").classes("queue-expansion"):
            render_activity_panel()


def render_active_view() -> None:
    render_view_toolbar()
    match state.active_view:
        case "sources":
            render_sources_view()
        case "search":
            render_search_view()
        case "graph":
            render_graph_view()
        case "lint":
            render_lint_view()
        case "review":
            render_review_view()
        case "settings":
            render_settings_view()
        case _:
            render_wiki_view()


def view_title() -> str:
    return {
        "wiki": "Wiki",
        "sources": "Sources",
        "search": "Search",
        "graph": "Knowledge Graph",
        "lint": "Lint",
        "review": "Review",
        "settings": "Settings",
    }.get(state.active_view, "Wiki")


def render_view_toolbar() -> None:
    if state.active_view == "graph" and state.project is not None:
        nodes, edges = graph_service.build(state.project)
        with ui.row().classes("graph-toolbar"):
            ui.icon("hub").classes("toolbar-icon")
            ui.label("Knowledge Graph").classes("toolbar-title")
            ui.label(f"{len(nodes)} pages").classes("toolbar-pill")
            ui.label(f"{len(edges)} links").classes("toolbar-pill")
            ui.space()
            for label, icon in (("Type", "sell"), ("Community", "bubble_chart"), ("Insight", "lightbulb")):
                ui.button(label, icon=icon).props("flat dense").classes("toolbar-button")
            ui.button(icon="refresh", on_click=app_view.refresh).props("flat round dense").tooltip("Refresh")
        return
    with ui.row().classes("content-toolbar"):
        ui.label(view_title()).classes("toolbar-title")
        ui.space()
        if state.selected_path:
            ui.label(state.selected_path).classes("path-label")


def render_wiki_view() -> None:
    render_chat_view()


def render_chat_view() -> None:
    assert state.project is not None
    conversations = chat_store.list_conversations(state.project.id)
    messages = chat_store.list_messages(state.active_conversation_id) if state.active_conversation_id else []
    with ui.row().classes("chat-shell"):
        with ui.column().classes("conversation-sidebar"):
            ui.button("New Chat", icon="add", on_click=create_chat_conversation).props("outline dense").classes("new-chat-button")
            with ui.column().classes("conversation-list"):
                if not conversations:
                    ui.label("No conversations yet").classes("empty-conversations")
                for conversation in conversations:
                    active = conversation["id"] == state.active_conversation_id
                    with ui.column().classes("conversation-item active-conversation" if active else "conversation-item"):
                        ui.button(
                            str(conversation["title"]),
                            on_click=lambda cid=str(conversation["id"]): select_conversation(cid),
                        ).props("flat dense align=left").classes("conversation-button")
                        ui.label(format_time(float(conversation["updated_at"]))).classes("conversation-meta")
        with ui.column().classes("chat-main"):
            if messages:
                with ui.column().classes("message-list"):
                    for message in messages:
                        ui.label(str(message["role"])).classes("message-role")
                        ui.markdown(str(message["content"])).classes("message-body")
            else:
                ui.space()
                ui.label("Start a conversation. LLM responses are not connected yet.").classes("chat-empty")
                ui.space()
            with ui.row().classes("chat-input-row"):
                message_input = ui.input(placeholder="Type a message...").classes("chat-input")
                ui.button(icon="send", on_click=lambda: add_chat_message(message_input.value)).props("round color=grey-6")


def create_chat_conversation() -> None:
    if state.project is None:
        return
    state.active_conversation_id = chat_store.create_conversation(state.project.id, "New Conversation")
    app_view.refresh()


def select_conversation(conversation_id: str) -> None:
    state.active_conversation_id = conversation_id
    app_view.refresh()


def add_chat_message(content: str | None) -> None:
    if state.project is None:
        return
    text = (content or "").strip()
    if not text:
        return
    if state.active_conversation_id is None:
        state.active_conversation_id = chat_store.create_conversation(state.project.id, text[:40] or "New Conversation")
    chat_store.add_message(state.active_conversation_id, "user", text)
    chat_store.add_message(
        state.active_conversation_id,
        "assistant",
        "LLM support is not connected yet. The message was saved locally.",
    )
    app_view.refresh()


def format_time(timestamp: float) -> str:
    from datetime import datetime

    return datetime.fromtimestamp(timestamp).strftime("%H:%M")


def render_sources_view() -> None:
    assert state.project is not None
    with ui.row().classes("source-toolbar"):
        ui.label("Raw Sources").classes("source-title")
        ui.space()
        ui.button(icon="refresh", on_click=app_view.refresh).props("flat round dense")
        source_path = ui.input(placeholder="Server file path").classes("source-path-input")
        ui.button(
            "Import",
            icon="add",
            on_click=lambda: import_source_from_input(source_path.value),
        ).props("dense unelevated color=dark")
        ui.button(
            "Folder",
            icon="create_new_folder",
            on_click=lambda: set_status("Folder import is planned for the next phase"),
        ).props("dense unelevated color=dark")
    source_nodes = project_source_rows(state.project)
    with ui.column().classes("source-list"):
        for node in source_nodes:
            render_source_row(node)


def project_source_rows(project: WikiProject) -> list[FileNode]:
    rows: list[FileNode] = []
    for rel in ("purpose.md", "schema.md"):
        path = files.resolve(project, rel)
        if path.is_file():
            rows.append(FileNode(name=path.name, path=rel, is_dir=False))
    rows.extend(files.list_tree(project, "raw/sources"))
    return rows


def import_source_from_input(path: str | None) -> None:
    if state.project is None or not path:
        return
    try:
        rel = files.import_source(state.project, path)
        set_status(f"Imported {rel}")
        app_view.refresh()
    except Exception as exc:
        set_status(f"Import failed: {exc}")


def render_source_node(node: FileNode, depth: int = 0) -> None:
    padding = f"padding-left: {depth * 14}px"
    if node.is_dir:
        ui.label(f"▾ {node.name}").classes("tree-dir").style(padding)
        for child in node.children:
            render_source_node(child, depth + 1)
    else:
        ui.button(
            node.name,
            icon="insert_drive_file",
            on_click=lambda p=node.path: select_file(p),
        ).props("flat dense align=left").classes("tree-file").style(padding)


def render_source_row(node: FileNode, depth: int = 0) -> None:
    if node.is_dir:
        ui.label(f"⌄ {node.name}").classes("source-folder").style(f"padding-left: {depth * 16}px")
        for child in node.children:
            render_source_row(child, depth + 1)
        return
    with ui.row().classes("source-row"):
        ui.button(
            node.name,
            icon="description",
            on_click=lambda p=node.path: select_file(p),
        ).props("flat dense align=left").classes("source-row-title").style(f"padding-left: {depth * 16}px")
        ui.space()
        ui.button(icon="menu_book", on_click=lambda p=node.path: select_file(p)).props("flat round dense").tooltip("Preview")
        ui.button(icon="delete", on_click=lambda p=node.path: delete_source(p)).props("flat round dense").tooltip("Delete")


def delete_source(path: str) -> None:
    if state.project is None:
        return
    if path in {"purpose.md", "schema.md"} or path.startswith("wiki/"):
        set_status("This file is part of the project schema and was not deleted.")
        return
    try:
        files.delete(state.project, path)
        if state.selected_path == path:
            clear_selection()
        set_status(f"Deleted {path}")
        app_view.refresh()
    except Exception as exc:
        set_status(f"Delete failed: {exc}")


def render_search_view() -> None:
    query = ui.input("Search wiki and sources", value=state.search_query).classes("full")
    query.on("update:model-value", lambda event: setattr(state, "search_query", event.args))
    ui.button("Search", icon="search", on_click=run_search).props("unelevated")
    meta = store.get_search_meta(state.project.id) if state.project else None
    if meta:
        ui.label(f"Last indexed {meta['document_count']} documents").classes("muted small")
    for result in state.search_results:
        with ui.card().classes("result-card"):
            ui.button(
                result.title,
                on_click=lambda path=result.path: select_file(path),
            ).props("flat align=left").classes("result-title")
            ui.label(result.path).classes("path-label")
            ui.label(result.excerpt).classes("muted")
            ui.label(f"score {result.score:g}").classes("small")


def run_search() -> None:
    if state.project is None:
        return
    state.search_results = search_service.search(state.project, state.search_query)
    app_view.refresh()


def render_graph_view() -> None:
    assert state.project is not None
    nodes, edges = graph_service.build(state.project)
    if not nodes:
        ui.label("No wiki pages found yet.").classes("muted")
        return
    focus_nodes = {edge.source for edge in edges} | {edge.target for edge in edges}
    chart_nodes = [
        {
            "id": node.id,
            "name": node.label,
            "value": node.path,
            "category": node.kind,
            "symbolSize": 46 if node.kind == "index" else 36 if node.id in focus_nodes else 26,
        }
        for node in nodes
    ]
    categories = [{"name": kind} for kind in sorted({node.kind for node in nodes})]
    chart_edges = [
        {
            "source": edge.source,
            "target": edge.target,
            "value": edge.reason,
            "lineStyle": {"width": max(1.2, min(5, edge.weight)), "opacity": 0.38},
        }
        for edge in edges
    ]
    chart = ui.echart(
        {
            "tooltip": {"trigger": "item"},
            "legend": [{"data": [category["name"] for category in categories]}],
            "color": ["#5b9cf6", "#a970f7", "#ff8a3d", "#94a3b8", "#f6c344", "#18b6a4"],
            "series": [
                {
                    "type": "graph",
                    "layout": "force",
                    "roam": True,
                    "draggable": True,
                    "categories": categories,
                    "data": chart_nodes,
                    "links": chart_edges,
                    "label": {"show": True, "position": "right", "fontSize": 11, "fontWeight": 600},
                    "force": {"repulsion": 260, "edgeLength": 120, "gravity": 0.08},
                    "edgeSymbol": ["none", "none"],
                    "lineStyle": {"color": "#b7c7de", "curveness": 0.08, "opacity": 0.42},
                    "emphasis": {"focus": "adjacency", "lineStyle": {"width": 5}},
                }
            ],
        }
    ).classes("graph-chart")

    def handle_graph_click(event) -> None:
        args = event.args or {}
        data = args.get("data") if isinstance(args, dict) else None
        if isinstance(data, dict) and data.get("value"):
            select_file(str(data["value"]))

    chart.on("click", handle_graph_click)
    with ui.card().classes("legend-card"):
        ui.label("Node Types").classes("legend-title")
        for category in categories:
            ui.label(f"● {category['name']}").classes("legend-item")


def stat_card(label: str, value: str) -> None:
    with ui.card().classes("stat-card"):
        ui.label(value).classes("stat-value")
        ui.label(label).classes("small")


def render_lint_view() -> None:
    assert state.project is not None
    nodes, edges = graph_service.build(state.project)
    linked = {edge.source for edge in edges} | {edge.target for edge in edges}
    lonely = [node for node in nodes if node.id not in linked]
    ui.label("Static checks for the server-side Markdown project.").classes("muted")
    if not lonely:
        ui.label("No isolated wiki pages found.").classes("ok")
    else:
        ui.label(f"{len(lonely)} isolated pages").classes("section-title")
        for node in lonely:
            ui.button(node.path, on_click=lambda p=node.path: select_file(p)).props("flat align=left")


def render_review_view() -> None:
    assert state.project is not None
    ui.label("Review items are stored in SQLite. LLM-generated review creation is reserved for a later phase.").classes("muted")
    items = store.list_review_items(state.project.id)
    if not items:
        ui.label("No review items yet.").classes("ok")
    for item in items:
        with ui.card().classes("result-card"):
            ui.label(str(item["title"])).classes("section-title")
            ui.label(str(item["body"])).classes("muted")
            ui.label("resolved" if item["resolved"] else "pending").classes("small")


def render_settings_view() -> None:
    llm = settings.get("llm", DEFAULT_LLM_SETTINGS)
    ui.label("Workspace").classes("section-title")
    ui.label(str(config.workspace)).classes("path-label")
    ui.label("Database").classes("section-title")
    ui.label(str(config.database)).classes("path-label")
    ui.separator()
    ui.label("LLM Settings").classes("section-title")
    provider = ui.input("Provider", value=llm.get("provider", "openai")).classes("full")
    model = ui.input("Model", value=llm.get("model", "")).classes("full")
    base_url = ui.input("Base URL", value=llm.get("base_url", "")).classes("full")
    api_key = ui.input("API Key", value=llm.get("api_key", ""), password=True).classes("full")

    def save_settings() -> None:
        settings.set(
            "llm",
            {
                "provider": provider.value,
                "model": model.value,
                "base_url": base_url.value,
                "api_key": api_key.value,
                "max_context_size": llm.get("max_context_size", 204800),
            },
        )
        set_status("Settings saved")

    ui.button("Save Settings", icon="save", on_click=save_settings).props("unelevated")


def render_right_panel() -> None:
    if not state.selected_path:
        ui.label("No document selected").classes("preview-title")
        ui.label("Select a wiki page, source, schema, or purpose file to preview it here.").classes("muted")
        return
    render_preview()


def render_preview() -> None:
    with ui.row().classes("preview-header"):
        ui.label((state.selected_path or "").rsplit("/", 1)[-1]).classes("preview-title")
        ui.space()
        if state.preview_editing:
            ui.button("Save", icon="save", on_click=save_selected_file).props("unelevated dense").classes("edit-button")
        else:
            ui.button("Edit", icon="edit", on_click=enable_preview_edit).props("outline dense").classes("edit-button")
        ui.button(icon="close", on_click=clear_selection).props("flat round dense")
    ui.label(state.selected_path or "").classes("path-label")
    ui.separator()
    if state.selected_path and state.selected_path.lower().endswith((".md", ".markdown")):
        if state.draft.startswith("Error loading file:"):
            ui.label(state.draft).classes("error-text")
        elif state.preview_editing:
            editor = ui.textarea(value=state.draft).classes("preview-editor")
            editor.on("update:model-value", lambda event: setattr(state, "draft", event.args))
        else:
            render_document_outline(state.draft)
            ui.markdown(state.draft).classes("preview")
            render_wikilink_buttons(state.draft)
    else:
        ui.label(state.draft[:4000]).classes("pre-wrap")


def clear_selection() -> None:
    state.selected_path = None
    state.draft = ""
    state.preview_editing = False
    app_view.refresh()


def enable_preview_edit() -> None:
    state.preview_editing = True
    app_view.refresh()


def render_document_outline(markdown: str) -> None:
    headings = [
        line.lstrip("# ").strip()
        for line in markdown.splitlines()
        if line.startswith("#")
    ]
    if not headings:
        return
    ui.label("Outline").classes("section-title")
    for index, heading in enumerate(headings[:10], start=1):
        ui.label(f"{index}. {heading}").classes("outline-item")


def render_wikilink_buttons(markdown: str) -> None:
    links = sorted(set(wikilinks(markdown)), key=str.lower)
    if not links:
        return
    ui.label("Wiki links").classes("section-title")
    with ui.row().classes("wikilink-row"):
        for link in links:
            target = find_wiki_page(link)
            ui.button(
                link,
                icon="link",
                on_click=lambda value=link: jump_to_wikilink(value),
            ).props("outline dense").classes("" if target else "missing-link")


STYLE = """
<style>
html, body, #app { height: 100%; margin: 0; background: #ffffff; }
.full-screen { min-height: 100vh; }
.center-screen { align-items: center; justify-content: center; }
.welcome-bg { background: #f8fafc; }
.welcome-card { width: min(560px, 92vw); padding: 28px; border-radius: 8px; }
.title { font-size: 32px; font-weight: 700; }
.view-title { font-size: 18px; font-weight: 700; }
.project-title { font-size: 16px; font-weight: 700; }
.project-path, .path-label { color: #64748b; font-size: 12px; overflow-wrap: anywhere; }
.section-title { font-weight: 650; margin-top: 8px; }
.muted { color: #64748b; }
.small { color: #64748b; font-size: 12px; }
.ok { color: #047857; font-weight: 600; }
.full { width: 100%; }
.gap { gap: 8px; }
.desktop-shell { height: 100vh; width: 100vw; overflow: hidden; gap: 0; }
.window-bar { height: 24px; min-height: 24px; width: 100%; background: #e6e6e6; border-bottom: 1px solid #d5d5d5; align-items: center; justify-content: center; position: relative; }
.traffic-lights { position: absolute; left: 10px; gap: 6px; align-items: center; }
.traffic-dot { width: 10px; height: 10px; border-radius: 999px; display: inline-block; }
.traffic-dot.red { background: #d8d8d8; }
.traffic-dot.amber { background: #cfcfcf; }
.traffic-dot.green { background: #c7c7c7; }
.window-title { color: #8a8a8a; font-size: 11px; font-weight: 700; }
.app-shell { height: 100vh; width: 100vw; gap: 0; flex-wrap: nowrap; overflow: hidden; background: #ffffff; align-items: stretch; }
.nav-region { width: 350px; min-width: 220px; max-width: 520px; height: 100%; gap: 0; flex: 0 0 auto; flex-wrap: nowrap; overflow: auto; resize: horizontal; border-right: 1px solid #e5e7eb; }
.icon-sidebar { width: 36px; min-width: 36px; height: 100%; padding: 8px 2px; background: #fbfcfe; border-right: 1px solid #e5e7eb; align-items: center; gap: 6px; }
.logo { width: 26px; height: 26px; border-radius: 6px; background: #e8eef7; color: #4b69a6; display: flex; align-items: center; justify-content: center; font-weight: 800; font-size: 14px; }
.left-pane { flex: 1 1 auto; min-width: 0; height: 100%; overflow: auto; padding: 0 10px 12px; flex-wrap: nowrap; background: #ffffff; }
.main-pane { flex: 1 1 auto; min-width: 360px; height: 100%; overflow: hidden; padding: 0; flex-wrap: nowrap; background: #ffffff; }
.right-pane { width: 400px; min-width: 260px; max-width: 55vw; height: 100%; overflow: auto; border-left: 1px solid #e5e7eb; padding: 12px 14px; flex: 0 0 auto; flex-wrap: nowrap; background: #ffffff; resize: horizontal; direction: rtl; }
.right-pane > * { direction: ltr; }
.sidebar-tabs { height: 42px; min-height: 42px; align-items: flex-end; justify-content: center; gap: 16px; border-bottom: 1px solid #e5e7eb; margin: 0 -10px 10px; }
.sidebar-tab { color: #64748b; border-radius: 0; font-size: 12px; min-height: 32px; }
.active-tab { color: #111827; border-bottom: 2px solid #111827; font-weight: 700; }
.tree-dir { color: #475569; font-size: 12px; margin-top: 6px; font-weight: 650; }
.tree-section-row { width: 100%; align-items: center; gap: 6px; flex-wrap: nowrap; margin-top: 8px; }
.tree-count { color: #64748b; font-size: 11px; }
.tree-file { width: 100%; justify-content: flex-start; color: #475569; min-height: 24px; font-size: 12px; border-radius: 4px; }
.selected-file { background: #edf4ff; color: #1d4ed8; }
.editor textarea { min-height: calc(100vh - 190px) !important; font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace; font-size: 13px; line-height: 1.55; }
.preview-editor textarea { min-height: calc(100vh - 95px) !important; font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace; font-size: 13px; line-height: 1.55; }
.preview { line-height: 1.62; color: #334155; font-size: 13px; }
.preview h1 { font-size: 18px; }
.preview h2 { font-size: 15px; }
.pre-wrap { white-space: pre-wrap; font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace; }
.recent-row { width: 100%; align-items: center; gap: 10px; flex-wrap: nowrap; }
.recent-button { min-width: 150px; justify-content: flex-start; }
.recent-path { color: #64748b; font-size: 12px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.result-card { width: 100%; margin-top: 10px; border-radius: 8px; }
.result-title { font-weight: 700; justify-content: flex-start; }
.stats-row { gap: 10px; }
.stat-card { min-width: 120px; border-radius: 8px; }
.stat-value { font-size: 28px; font-weight: 700; }
.content-toolbar, .graph-toolbar { height: 42px; min-height: 42px; width: 100%; align-items: center; gap: 8px; padding: 0 12px; border-bottom: 1px solid #e5e7eb; background: #ffffff; flex-wrap: nowrap; }
.toolbar-icon { color: #475569; font-size: 17px; }
.toolbar-title { font-weight: 700; font-size: 13px; color: #1f2937; white-space: nowrap; }
.toolbar-pill { background: #f1f5f9; border: 1px solid #e2e8f0; color: #64748b; border-radius: 999px; padding: 2px 8px; font-size: 11px; }
.toolbar-button { color: #334155; font-size: 12px; }
.graph-grid { width: 100%; gap: 10px; display: none; }
.graph-node { border-radius: 8px; min-height: 110px; }
.graph-chart { width: 100%; height: calc(100vh - 72px); border: 0; border-radius: 0; margin: 0; background: #fbfdff; }
.legend-card { position: absolute; left: 266px; bottom: 18px; width: 135px; border-radius: 6px; padding: 8px !important; opacity: 0.94; }
.legend-title { font-size: 11px; font-weight: 700; color: #334155; }
.legend-item { font-size: 11px; color: #64748b; line-height: 1.3; }
.preview-header { align-items: center; gap: 8px; flex-wrap: nowrap; }
.preview-title { font-size: 13px; font-weight: 700; color: #111827; overflow-wrap: anywhere; }
.edit-button { font-size: 11px; }
.outline-item { color: #475569; font-size: 12px; line-height: 1.4; margin-left: 4px; }
.wikilink-row { gap: 8px; margin-top: 6px; }
.missing-link { opacity: 0.55; }
.chat-shell { width: 100%; height: calc(100vh - 42px); gap: 0; flex-wrap: nowrap; overflow: hidden; background: #ffffff; }
.conversation-sidebar { width: 200px; min-width: 200px; height: 100%; border-right: 1px solid #e5e7eb; background: #f8fafc; padding: 8px; flex-wrap: nowrap; }
.new-chat-button { width: 100%; min-height: 30px; border-radius: 6px; background: #ffffff; }
.conversation-list { width: 100%; gap: 8px; padding-top: 14px; }
.conversation-group { gap: 2px; padding: 0 4px; }
.conversation-day { font-size: 12px; font-weight: 700; color: #111827; }
.conversation-meta { font-size: 11px; color: #94a3b8; }
.conversation-item { width: 100%; gap: 2px; padding: 9px 10px; border-radius: 6px; }
.active-conversation { background: #e5e7eb; }
.conversation-title { font-size: 12px; font-weight: 700; color: #111827; }
.conversation-button { width: 100%; justify-content: flex-start; font-size: 12px; font-weight: 700; color: #111827; padding: 0; min-height: 18px; }
.empty-conversations, .chat-empty { color: #94a3b8; font-size: 12px; text-align: center; padding: 18px 8px; }
.chat-main { flex: 1; min-width: 0; height: 100%; background: #ffffff; padding: 0 12px 10px; flex-wrap: nowrap; }
.message-list { flex: 1; width: 100%; overflow: auto; padding: 18px 0; gap: 8px; }
.message-role { color: #64748b; font-size: 11px; text-transform: uppercase; font-weight: 700; }
.message-body { max-width: 720px; padding: 10px 12px; border: 1px solid #e5e7eb; border-radius: 8px; background: #f8fafc; }
.chat-input-row { width: 100%; align-items: center; gap: 8px; border-top: 1px solid #e5e7eb; padding-top: 10px; flex-wrap: nowrap; }
.chat-input { flex: 1; }
.chat-input .q-field__control { min-height: 38px; height: 38px; border-radius: 8px; }
.source-toolbar { height: 44px; min-height: 44px; align-items: center; gap: 8px; padding: 0 12px; border-bottom: 1px solid #e5e7eb; background: #ffffff; flex-wrap: nowrap; }
.source-title { font-size: 13px; font-weight: 700; color: #111827; }
.source-path-input { width: 250px; }
.source-path-input .q-field__control { height: 30px; min-height: 30px; }
.source-list { width: 100%; padding: 22px 16px; gap: 4px; }
.source-row { width: 100%; min-height: 32px; align-items: center; flex-wrap: nowrap; border-radius: 4px; }
.source-row:hover { background: #f8fafc; }
.source-row-title { flex: 0 0 360px; justify-content: flex-start; color: #475569; font-size: 13px; }
.source-folder { color: #334155; font-size: 12px; font-weight: 700; margin-top: 8px; }
.empty-state { color: #64748b; padding: 24px 16px; }
</style>
"""


def run() -> None:
    host = os.environ.get("LLMWIKI_HOST", "127.0.0.1")
    port = int(os.environ.get("LLMWIKI_PORT", "8080"))
    ui.run(title="LLM Wiki", host=host, port=port, reload=False)


if __name__ in {"__main__", "__mp_main__"}:
    run()
