---
id: F004
query: Analyze flowtask documentation system as reference
type: read
---

## Architecture
MkDocs-based static site with Material theme. NOT a programmatic extraction system.

## Key Points
- Uses `mkdocstrings` + `mkdocs-gen-files` plugins
- `gen_ref_pages.py` auto-discovers .py files and generates `::: module.path` stubs
- Docstrings follow Google style with RST property tables + YAML examples
- Served as static files at `/documentation/` via aiohttp `add_static`

## Docstring Convention (Reference)
Components use:
- **Name** (class name)
- **Overview** (description)
- **Properties table** (name, required, summary)
- **Returns** description
- **Example** (YAML config block)

## What Flowtask Does NOT Have
- No JSON schema generation from class definitions
- No programmatic component discovery API endpoint
- No Pydantic-based validation
- No `inspect`-based attribute extraction in docs tooling

## Conclusion
Flowtask's docs system is purely MkDocs/static. For FEAT-095 we need a
programmatic approach: class introspection → JSON schema → CLI + HTTP.
The flowtask docstring convention is a useful style guide but not code to reuse.
