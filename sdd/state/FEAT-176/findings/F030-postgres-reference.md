---
id: F030
query_id: Q030
type: web
executed_at: 2026-09-15T01:11:03Z
parent_id: null
depth: 1
---

# F030 — postgres-reference

## Summary

PostgreSQL information_schema.columns includes view columns and shows only columns visible to the connected role. Schemas permit same-named objects but are not themselves an access-control boundary. Discovery should distinguish actual tables and require explicit privileges.

## Citations

- PostgreSQL 18: [information_schema.columns](https://www.postgresql.org/docs/18/infoschema-columns.html), paragraph describing visibility and view columns.
- PostgreSQL 18: [Schemas](https://www.postgresql.org/docs/18/ddl-schemas.html), schema privileges and schema-qualified access.

## Notes

These are upstream semantics, not verification of the deployment's PostgreSQL version or grants.


## Audit note

Related searches and targeted ranges were batched. Citations represent inspected ranges or exact search matches, not a claim that every cited file was read in full. The timestamp records digest assembly at research completion.
