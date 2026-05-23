# TASK-681: Airtable source documentation (`docs/sources/airtable.md`)

**Feature**: FEAT-096 — Multi-Query ThreadSource: Airtable
**Spec**: `sdd/specs/multi-threadsource-airtable.spec.md`
**Status**: pending
**Priority**: low
**Estimated effort**: S (< 2h)
**Depends-on**: TASK-676
**Assigned-to**: unassigned

---

## Context

Adds a user-facing document covering: configuration shape, env vars, PAT vs. OAuth setup, and how to enable the OAuth callback routes. Without this, operators have to read the spec + tests to figure out how to use the new source.

Implements §3 Module 6 of the spec.

---

## Scope

Create `docs/sources/airtable.md` with the following sections (each kept tight):

1. **Overview** — one paragraph: what `AirtableSource` does, what it returns (pandas DataFrame), where it fits in MultiQuery pipelines.
2. **YAML / JSON configuration** — show both shapes:
   - URL form: `source.url`
   - Explicit form: `source.base_id`, `source.table`, `source.view`
   - Optional API-side filters: `filter_by_formula`, `max_records`, `page_size`
3. **Auth modes** — explain the precedence: session OAuth → `AIRTABLE_ACCESS_TOKEN` PAT → error. Make clear that the PAT is **global / server-wide** (not per-user) and link to spec §1 Non-Goals.
4. **Environment variables** — table of:
   - `AIRTABLE_CLIENT_ID` / `AIRTABLE_CLIENT_SECRET` (OAuth app credentials)
   - `AIRTABLE_ACCESS_TOKEN` (PAT)
   - `AIRTABLE_BASE_ID` (default base id, optional)
   - `AIRTABLE_REDIRECT_URI` (must match Airtable OAuth app registration)
   - `QS_AIRTABLE_OAUTH_ENABLED` (feature flag, defaults to `False`)
5. **Enabling the OAuth flow** — step-by-step:
   - Register an OAuth app on Airtable's developer console.
   - Set the redirect URI to `<your-host>/api/v1/qs/integrations/airtable/callback`.
   - Set `AIRTABLE_CLIENT_ID`, `AIRTABLE_CLIENT_SECRET`, and `AIRTABLE_REDIRECT_URI` (the latter must exactly match).
   - Set `QS_AIRTABLE_OAUTH_ENABLED=true`.
   - Restart QuerySource — verify `/api/v1/qs/integrations/airtable/connect` responds 200.
   - Users visit `/connect`, click "Connect to Airtable", complete consent. Tokens land in their `navigator_session` under the key `airtable`.
6. **Token storage & reconnect UX** — short paragraph: tokens live in `navigator_session`; on expiry the Interface refreshes once; on refresh failure the Source raises `AirtableReauthRequired` and the user must revisit `/connect`.
7. **Known limitations** — surface the items from spec §7 Known Risks (no streaming, 100MB warning, 429 raises rather than retrying, no streaming/incremental pagination, field-type passthrough — linked records and attachments stay as raw JSON for now).
8. **Examples** — a single YAML example showing a MultiQuery pipeline with `AirtableSource` joined with another source (use `TableSource` from FEAT-093 for the join target).

Keep total length ≤ 200 lines. Use code fences with explicit `yaml` / `python` / `bash` language tags. No images.

**NOT in scope**:
- Documenting write methods — they are stubs and explicitly out-of-scope (cross-reference spec §1 Non-Goals only).
- Documenting how Airtable's pricing or API quotas work — link to Airtable's own docs.
- Updating the project README — out of scope.

---

## Files to Create / Modify

| File | Action | Description |
|---|---|---|
| `docs/sources/airtable.md` | CREATE | New user-facing doc |

If `docs/sources/` does not exist yet, create it (no `__init__.md` or index file is required by the project — verified: `ls docs/` shows no enforced index).

---

## Codebase Contract (Anti-Hallucination)

### Verified References

```bash
# Verify docs/ structure before writing:
ls docs/                                              # docs/sdd/ exists; no docs/sources/ yet
ls docs/sdd/                                          # WORKFLOW.md, etc.
grep -l "SOURCE_REGISTRY" docs/ -r 2>/dev/null        # may surface other source docs to mirror style
```

### Style Precedent

- Spec lives at `sdd/specs/multi-threadsource-airtable.spec.md` — link to its anchors (`#1-motivation--business-requirements`, etc.) using GitHub's auto-anchor conventions.
- Other source documentation (if any) under `docs/` should be the style reference. If none exists, use plain GitHub-flavored Markdown.

### Does NOT Exist

- ~~A `docs/sources/<name>.md` precedent for SmartSheet / SharePoint / S3~~ — verified via `ls docs/sources/` (the directory itself is new). This file will be the first in that folder; do NOT pretend to mirror a non-existent style.
- ~~A `mkdocs.yml` or `sphinx` config that requires registering the new page~~ — none in repo (verified: `ls mkdocs.yml conf.py 2>/dev/null` returns nothing for either at the repo root for docs purposes).

---

## Implementation Notes

### Key Constraints

- The leaked PAT (`pat36EoFVW…`) must NOT appear in any example. Use the env-var name `AIRTABLE_ACCESS_TOKEN` only.
- Examples must compile / parse — copy YAML from `tests/multi/sources/test_airtable_source.py` (the `_opts_url()` fixture) so the doc tracks reality.
- Cross-link the spec for non-obvious decisions (PAT scope, reauth UX) so readers can find the source-of-truth without searching.

### References

- Spec sections to cite: §1 (motivation), §1 Non-Goals (PAT scope), §2 (architecture overview), §5 (acceptance criteria — for behavior), §8 Open Questions (field normalization caveat).

---

## Acceptance Criteria

- [ ] `docs/sources/airtable.md` exists and contains all 8 sections listed in Scope.
- [ ] All YAML examples in the doc pass a `python -c "import yaml; yaml.safe_load(open('<doc>'))"` lite check OR at minimum match the shape of `tests/multi/sources/test_airtable_source.py` fixtures.
- [ ] The doc mentions `AirtableReauthRequired` by name (so a user `grep`-ing for the exception finds the doc).
- [ ] The doc explains that `QS_AIRTABLE_OAUTH_ENABLED` is **off by default** and PAT-only operation works without enabling it.
- [ ] The leaked PAT string `pat36EoFVW` is NOT present in any committed doc file.
- [ ] Doc length is between 80 and 200 lines (inclusive — not a stub, not a wall of text).
- [ ] Markdown is well-formed — no broken links, no malformed code fences.

---

## Test Specification

No code tests. Manual review only. The acceptance criteria above are checkable via:

```bash
# Existence + size
test -s docs/sources/airtable.md && wc -l docs/sources/airtable.md

# Required terms
grep -q "AirtableReauthRequired" docs/sources/airtable.md
grep -q "QS_AIRTABLE_OAUTH_ENABLED" docs/sources/airtable.md
grep -q "AIRTABLE_ACCESS_TOKEN" docs/sources/airtable.md

# Leaked token absent
! grep -q "pat36EoFVW" docs/sources/airtable.md
```

---

## Agent Instructions

1. Confirm `TASK-676` is `completed` (final shape of `AirtableSource` settled).
2. `mkdir -p docs/sources/` if needed.
3. Write the doc per Scope, drawing examples directly from the test fixtures.
4. Run the checks listed in Test Specification.
5. Move to `sdd/tasks/completed/` and update index.

---

## Completion Note

**Completed by**: Claude Sonnet 4.6 (SDD Worker)
**Date**: 2026-05-22
**Notes**: All 8 sections written. 181 lines (within 80-200 bound). All required
terms present; no leaked PAT. docs/sources/ directory created as first source doc.
**Deviations from spec**: None. docs/sources/ was not pre-existing as confirmed by spec.
