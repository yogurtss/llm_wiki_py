# LLM Wiki Python

Python migration of LLM Wiki. This repository contains the Python app, local storage/services, tests, and the Python bridge foundation for a future React/pywebview desktop shell.

The original `llm_wiki/` React/Tauri source is intentionally not included in this repository.

## Requirements

- Python 3.11+
- pip

## Install

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
```

## Start The Current Python UI

Run the NiceGUI Python app:

```bash
python -m app.main
```

Then open:

```text
http://127.0.0.1:8080
```

Useful environment variables:

```bash
LLMWIKI_HOST=127.0.0.1
LLMWIKI_PORT=8080
LLMWIKI_HOME=./data
LLMWIKI_WORKSPACE=./data/projects
LLMWIKI_DB=./data/llmwiki.sqlite3
```

Example with a custom port:

```bash
LLMWIKI_PORT=8090 python -m app.main
```

## Start The Python Bridge Shell

`app.desktop` starts the Python `/api/invoke` bridge and can serve a built React UI from `dist/` or `llm_wiki/dist/`.

Browser mode:

```bash
python -m app.desktop --browser
```

Desktop mode with pywebview:

```bash
python -m app.desktop
```

If no React build exists, the bridge still starts, but it only serves a small status page. To use this mode with the original UI, place the React production build at:

```text
dist/index.html
```

or:

```text
llm_wiki/dist/index.html
```

## Run Tests

```bash
pytest
```

## Project Data

By default, runtime data is written under `./data`:

- `data/projects/` for wiki projects
- `data/llmwiki.sqlite3` for local app state

These files are ignored by git.
