---
kind: inline
jira_key: null
fetched_at: 2026-09-29T15:45:00Z
summary_oneline: Lazy-import output writers (esp. PDF/weasyprint) to cut QuerySource startup time in library and HTTP server modes
---

lazy-import-writers -- actualmente todos los writers (includo PDF que depende de weasyprint) hace un import on startup, lo que ralentiza la carga de Querysource en modo normal y servidor http
