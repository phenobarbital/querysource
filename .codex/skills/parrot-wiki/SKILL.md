---
name: parrot-wiki
description: Query the repository LLM-wiki before raw source scans, and save durable knowledge into it.
---

# Parrot Wiki

Start codebase investigations with `wikitoolkit query "<focused question>"`,
then use `wikitoolkit page <id>` and `wikitoolkit related <id>`. Fall back to
raw search only after those paths are empty.

The wiki is also persistent memory. Save durable facts, decisions, and lessons
with `wikitoolkit remember "<fact>" --category <note|decision|lesson|concept>`.
Use `wikitoolkit note`, `wikitoolkit link`, `wikitoolkit memories`, and
`wikitoolkit audit` to maintain and review that knowledge.
Use `wikitoolkit standup` for day/week/month briefs and `wikitoolkit entity`
for typed authoring, listing and local-plane back-fill.
