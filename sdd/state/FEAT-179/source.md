---
kind: inline
jira_key: null
fetched_at: 2026-10-01T01:34:18+02:00
summary_oneline: Add NOT (@!) and OR-if-not-exists (@$) operator support to JSONB where_cond filtering (AND/OR already exist)
---

# not-or-querysource-support

On JSONB-based columns, we added AND and OR support, but we need to add
NOT (and OR if not exists) support, using an example like:

```json
"where_cond": {
    "@!": [
        {
            "course": "Pilates Studio"
        }, {
            "course": "Pilates Mat"
        }
    ],
    "@$": [
        {
            "course": "Pilates Studio"
        }, {
            "course": "Pilates Mat"
        }
    ]
}
```

Interpretation of requested operators:
- `@!` — NOT: exclude rows matching any of the listed JSONB conditions.
- `@$` — OR (requested as "OR if not exists"): to be clarified against
  the existing OR implementation during research.
