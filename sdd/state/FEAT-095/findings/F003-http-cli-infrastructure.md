---
id: F003
query: Existing HTTP and CLI infrastructure
type: read
---

## Web Framework
- aiohttp via Navigator (`navigator.handlers.types.AppHandler`)
- Routes registered in `querysource/services.py` on `app.router`

## Relevant Existing Routes
- `GET/POST /api/v3/queries/{slug}` — MultiQuery endpoint (QueryHandler)
- `POST /api/v3/queries` — MultiQuery inline execution
- No documentation or introspection endpoints exist

## CLI
- Single entry point: `query = "querysource.__cli__:main"` in pyproject.toml
- Implementation: `querysource/__cli__.py` — interactive PostgreSQL REPL
- No click/argparse/typer framework
- No doc-generation or component-listing CLI commands

## MultiQuery Pipeline Architecture
`MultiQS` class in `querysource/queries/multi/__init__.py`:
- Steps: queries/files → ThreadQuery/ThreadFile → Queue → Info → Join/Concat/Melt/Merge → Transform → Filter → GroupBy → Output(TableOutput)
- Config keys: `queries`, `files`, `Info`, `Join`, `Concat`, `Melt`, `Merge`, `Filter`, `GroupBy`, `Transform`, `Output`, `Processors`
