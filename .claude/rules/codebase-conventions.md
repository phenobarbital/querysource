---
trigger: always_on
---

# QuerySource codebase conventions

Binding for every file you touch in this repository. If a task file and this
document disagree, STOP and report — never pick one silently.

## Stack
- HTTP: **aiohttp** + **navigator-api**, served by **gunicorn** (aiohttp worker). There is no ASGI stack.
- Async-first: I/O paths are `async def`; never block the event loop (no `time.sleep`, no sync HTTP or DB drivers in async code).
- Database access goes through `asyncdb` via `QueryConnection` (`querysource/connections.py`) and a `BaseProvider` subclass (`querysource/providers/abstract.py`); external APIs subclass `externalProvider` (`querysource/providers/external.py`). Never call a driver SDK directly from a handler.
- Queries are dispatched by slug through `QS` (`querysource/queries/qs.py`); query strings are rendered by an `AbstractParser` subclass (`querysource/parsers/`, Cython `.pyx`) — never build SQL/NoSQL with string concatenation of user input.
- Configuration and secrets come from `navconfig` (`querysource/conf.py`). Logging is `self.logger` (`logging.getLogger(__name__)`), never `print`.
- Structured data uses `datamodel` (`BaseModel`) or Pydantic, following the surrounding module.

## Forbidden — and what to use instead
| Never import / use | Use instead |
|---|---|
| `requests` in new code | `aiohttp` (see `querysource/interfaces/http.py`); `httpx` only where the module already uses it |
| `starlette`, `fastapi`, `uvicorn` | aiohttp handlers under `querysource/handlers/`, served by gunicorn |
| `langchain`, `langgraph` | not part of this library |
| `print(...)` | `self.logger.<level>(...)` |
| `pip`, `poetry`, `requirements.txt` | `uv add` / `uv pip`, dependencies in `pyproject.toml` |
| hardcoded credentials / DSNs | `navconfig` config values and environment variables |

## Repository layout
- Single package: source lives in `querysource/` at the repo root (not a uv workspace).
- Providers: `querysource/providers/`; parsers (Cython): `querysource/parsers/`; outputs/writers: `querysource/outputs/`; multi-query: `querysource/queries/multi/`; handlers: `querysource/handlers/`.
- Rust extensions (PyO3/maturin) live in `rust/`. Changing a `.pyx` or Rust source requires rebuilding the extension before tests see it.
- Tests live in `tests/` (pytest, `asyncio_mode = auto` in `pytest.ini`).

## Tooling — pick the mode that matches how you run
- **Interactive shell** (humans, Claude Code, codex, agy): `source .venv/bin/activate` first; then `uv add` / `uv pip`, `pytest`, `ruff check`.
- **Tool-driven coder without a shell** (dev-loop in-process seats): there is no shell — never try `source`, `cd`, pipes or `>`. Call the allowlisted binaries directly (`pytest`, `ruff`, `python`, `uv`); the host resolves them on its `PATH`. Do not create or look for a `.venv` inside your worktree — it is a bare git worktree.
- Common: `ruff check` is the lint gate; `pylint` uses the repo `.pylintrc`; tests use `pytest` + `pytest-asyncio`; run your task's tests before committing.

## Code standards
- Google-style docstrings and strict type hints on every function and class.
- `snake_case` functions/variables, `PascalCase` classes (legacy provider/writer names such as `externalProvider`, `jsonWriter` keep their existing casing).
- Secrets come only from environment variables / navconfig — never in code or committed files.
- Complete, working files: no `TODO`, no stubs, no "existing code here" placeholders.
- Minimal, focused diffs: touch only the files your task lists; never refactor outside scope.
