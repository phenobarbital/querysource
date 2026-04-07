# Feature Specification: PodcastMaker Component

**Feature ID**: FEAT-007
**Date**: 2026-03-23
**Author**: juanfran
**Status**: approved
**Target version**: 5.11.0

---

## 1. Motivation & Business Requirements

### Problem Statement

Generating podcast-style audio content from data pipelines (weekly reports, product
summaries, review digests, etc.) currently requires a custom one-off script or
manual steps — there is no reusable FlowTask component that can:

1. Accept any tabular text as input.
2. Transform it into a conversational two-speaker script via LLM.
3. Synthesise the script to audio using Google TTS.
4. Return file paths in a DataFrame so downstream steps (e.g. `TableOutput`,
   `SendNotify`) can store or distribute the results.

`ProductReportBot` handles podcasts as one of three output formats for product data
specifically. There is no general-purpose podcast component available to all programs.

### Goals

- Provide a general-purpose `PodcastMaker` component usable in any YAML task.
- Delegate all script generation and TTS synthesis to `BasicAgent.speech_report()` —
  no re-implementation of audio or script logic.
- Prompt loaded from `{agent_id}/prompts/` via `AgentBase.open_prompt()`, consistent
  with other agent-based components.
- Output a DataFrame with `podcast_path` and `script_path` columns so results can
  be stored in DB or distributed via `SendNotify`.
- Configurable LLM (provider, model, temperature) for script generation via YAML.
- Configurable speakers (name, role, characteristic, gender, optional voice) via YAML,
  applied directly to `BasicAgent.speakers` after agent creation.

### Non-Goals

- Does not fetch or scrape source data — expects a DataFrame from a previous step.
- Does not generate PDFs or PowerPoint slides (that is `ProductReportBot`'s scope).
- Does not stream audio in real-time (batch file generation only).
- Does not re-implement TTS or script formatting — fully delegated to `BasicAgent`.
- Does **not** control the TTS model — `speech_report()` always uses its own internal
  speech model regardless of the `llm:` block in YAML.

---

## 2. Architectural Design

### Overview

`PodcastMaker` uses the `AgentBase + FlowComponent` pattern, identical to `NextStopAgent`.
It is a thin wrapper — all the heavy lifting (script generation, TTS synthesis, file
management) is delegated to `BasicAgent.speech_report()`.

1. `start()` — validate input DataFrame, initialise `BasicAgent` via `AgentBase.start()`.
2. `run()` — iterate rows, call `self._agent.speech_report(text)` per row, collect paths.

No custom TTS code. No custom script formatting. No custom file management.

### Component Diagram

```
YAML Task
  │
  └─► PodcastMaker (AgentBase + FlowComponent)
          │
          ├─ start()
          │    └─► AgentBase.start()
          │            └─► BasicAgent.configure()
          │
          └─ run()  [one iteration per DataFrame row]
               │
               └─► BasicAgent.speech_report(
                       report=source_text,
                       max_lines=self.max_lines,
                       num_speakers=self.num_speakers,
                       podcast_instructions=self.podcast_instructions,
                   )
                       │
                       ├─ LLM → conversational script
                       ├─ saves script → {agent_id}/generated_scripts/
                       └─ TTS → audio file → {agent_id}/podcasts/
                           │
                           └─► returns {podcast_path, script_path}
```

### Integration Points

| Existing Component | Integration Type | Notes |
|---|---|---|
| `AgentBase` | inherits | `flowtask/interfaces/parrot/agent.py` — lifecycle, `open_prompt()`, `create_agent()` |
| `FlowComponent` | inherits | `self.input`, `self.previous`, metrics, `_taskstore`, `_program` |
| `parrot.bots.agent.BasicAgent` | uses | `speech_report()` handles script gen + TTS + file save |
| `parrot.models.responses.AgentResponse` | uses | Default response type for the agent |

### Data Models

```yaml
PodcastMaker:
    text_column: summary               # required — source text column
    title_column: report_title         # optional — used for episode title in logs
    output_column: podcast_result      # column added to output DataFrame
    podcast_instructions: weekly.txt   # prompt filename or inline text
    max_lines: 20                      # max dialogue lines in script
    num_speakers: 2                    # number of speakers (max 2 for Gemini TTS)
    speakers:                          # optional — overrides BasicAgent defaults
      interviewer:
        name: Alex
        role: interviewer
        characteristic: engaging and curious
        gender: male
        voice: puck                    # optional — any TTSVoice value
      interviewee:
        name: Jordan
        role: interviewee
        characteristic: knowledgeable and concise
        gender: female
        voice: kore                    # optional
    llm:
      llm: google
      model: gemini-2.5-flash          # script generation only — NOT TTS model
      temperature: 0.8

# speech_report() return dict → expanded into two DataFrame columns:
# { "script_path": Path, "podcast_path": Path | None }
# Output row (per input row) — success:
{
  ...original_columns...,
  "podcast_result": "<source text>",
  "podcast_path":   "/path/to/{agent_id}/podcasts/script_<ts>.mp3",
  "script_path":    "/path/to/{agent_id}/generated_scripts/script_<ts>.txt",
  "podcast_error":  None,
}
# Output row (per input row) — failure:
{
  ...original_columns...,
  "podcast_result": "<source text>",
  "podcast_path":   None,
  "script_path":    None,
  "podcast_error":  "429 Resource exhausted: quota exceeded",   # full error message
}
```

### New Public Interfaces

```python
class PodcastMaker(AgentBase, FlowComponent):
    """General-purpose podcast generator for any tabular text source."""

    _agent_class: type = BasicAgent
    _agent_name: str = "PodcastMaker"
    agent_id: str = "podcast_maker"

    async def start(self, **kwargs) -> bool: ...
    async def run(self) -> pd.DataFrame: ...

    def _define_tools(self, base_dir: Path) -> list:
        return []  # No external tools needed
```

---

## 3. Module Breakdown

### Module 1: PodcastMaker Component

- **Path**: `flowtask/components/PodcastMaker.py`
- **Responsibility**: Thin FlowTask wrapper — validates input, runs `speech_report()` per row, returns DataFrame.
- **Depends on**: `AgentBase`, `FlowComponent`, `parrot.bots.agent.BasicAgent`

### Module 2: Failure Log

- **Path**: `{STATIC_DIR}/{agent_id}/podcast_failures/failures_<YYYYMMDD>.jsonl`
- **Responsibility**: Persistent audit log — one JSON line per failed episode, written at runtime. Allows operators to identify which titles/dates were not generated without inspecting the database.
- **Format** (one JSON object per line):
  ```json
  {
    "ts": "2026-03-23T14:05:33",
    "title": "Weekly Digest #12",
    "text_snippet": "Sales grew 12% last week...",
    "error": "429 Resource exhausted: quota exceeded",
    "retry": false
  }
  ```
- **Depends on**: Module 1

### Module 3: Unit Tests

- **Path**: `tests/test_podcast_maker.py`
- **Responsibility**: Tests for init, input validation, `speech_report()` delegation, empty row skipping, output DataFrame shape, failure logging.
- **Depends on**: Module 1, Module 2

---

## 4. Test Specification

### Unit Tests

| Test | Module | Description |
|---|---|---|
| `test_init_defaults` | Module 1 | Default params applied (`max_lines=20`, `num_speakers=2`) |
| `test_start_no_input` | Module 1 | `ComponentError` raised when no previous step |
| `test_start_missing_text_column` | Module 1 | `ConfigError` raised when `text_column` absent |
| `test_run_delegates_to_speech_report` | Module 1 | `_agent.speech_report()` called once per non-empty row |
| `test_run_skips_empty_rows` | Module 1 | Rows with empty `text_column` skipped without error |
| `test_run_returns_dataframe` | Module 1 | Output has `podcast_path` and `script_path` columns |
| `test_run_failure_continues` | Module 1 | `speech_report()` failure on one row does not abort remaining rows |
| `test_run_failure_row_in_output` | Module 1 | Failed row appears in output DataFrame with `podcast_path=None`, `script_path=None`, and `podcast_error` populated |
| `test_run_failure_writes_log` | Module 2 | Failed episode is appended to the JSONL failure log with `ts`, `title`, `text_snippet`, and `error` fields |
| `test_run_success_no_error_column` | Module 1 | Successful rows have `podcast_error=None` (column always present) |
| `test_define_tools_empty` | Module 1 | `_define_tools()` returns empty list |

### Integration Tests

| Test | Description |
|---|---|
| `test_end_to_end_mock_agent` | Full `run()` with mocked `BasicAgent.speech_report()` — verifies DataFrame shape and path values |

### Test Data / Fixtures

```python
@pytest.fixture
def sample_df():
    return pd.DataFrame({
        "title": ["Weekly Digest", "Monthly Summary"],
        "summary": ["Sales grew 12% last week...", "Q1 results exceeded targets..."],
    })

@pytest.fixture
def mock_agent():
    agent = MagicMock()
    agent.speech_report = AsyncMock(return_value={
        "podcast_path": "/tmp/podcast.mp3",
        "script_path": "/tmp/script.txt",
    })
    return agent
```

---

## 5. Acceptance Criteria

> This feature is complete when ALL of the following are true:

- [x] `flowtask/components/PodcastMaker.py` implemented with `AgentBase + FlowComponent` pattern.
- [x] All script/TTS logic delegated to `BasicAgent.speech_report()` — no re-implementation.
- [ ] All unit tests pass (`pytest tests/test_podcast_maker.py -v`).
- [ ] Component loadable in a real YAML task without errors.
- [ ] `podcast_path` and `script_path` appear in output DataFrame.
- [ ] Failed episodes do not abort the full run.
- [ ] Failed rows appear in the output DataFrame with `podcast_path=None`, `script_path=None`, and `podcast_error` set to the full exception message.
- [ ] Each failed episode is appended to `{STATIC_DIR}/{agent_id}/podcast_failures/failures_<YYYYMMDD>.jsonl` for post-run auditing.
- [ ] The `podcast_error` column is always present in the output DataFrame (value is `None` for successful rows).
- [ ] No changes to existing `ProductReportBot` or `NextStopAgent` behaviour.

---

## 6. Implementation Notes & Constraints

### Patterns to Follow

- Same `AgentBase + FlowComponent` MRO as `NextStopAgent` — follow that component as the reference.
- `_define_tools()` returns `[]` — `speech_report()` is self-contained in `BasicAgent`.
- `podcast_instructions` can be a filename (loaded via `BasicAgent.open_prompt()`) or
  inline text — `speech_report()` handles both cases transparently.
- Speakers are configurable from YAML via `self._agent.speakers = self._speakers_cfg`
  set in `start()` after `AgentBase.start()` creates the agent.
- Each speaker supports: `name`, `role`, `characteristic`, `gender`, and optional
  `voice` (any value from `parrot.models.google.TTSVoice`). Full voice list
  (Google Chirp 3 HD — source: Google Cloud official docs):

  | Voice | Gender | Characteristic |
  |---|---|---|
  | `achernar` | female | Soft |
  | `achird` | female | Friendly |
  | `algenib` | male | Gravelly |
  | `algieba` | male | Smooth |
  | `alnilam` | female | Firm |
  | `aoede` | female | Breezy |
  | `autonoe` | female | Bright |
  | `callirrhoe` | female | Easy-going |
  | `charon` | male | Informative |
  | `despina` | female | Smooth |
  | `enceladus` | male | Breathy |
  | `erinome` | female | Clear |
  | `fenrir` | male | Excitable |
  | `gacrux` | female | Mature |
  | `iapetus` | male | Clear |
  | `kore` | female | Firm |
  | `laomedeia` | female | Upbeat |
  | `leda` | female | Youthful |
  | `orus` | male | Firm |
  | `puck` | male | Upbeat |
  | `pulcherrima` | female | Forward |
  | `rasalgethi` | male | Informative |
  | `sadachbia` | female | Lively |
  | `sadaltager` | male | Knowledgeable |
  | `schedar` | female | Even |
  | `sulafat` | female | Warm |
  | `umbriel` | male | Easy-going |
  | `vindemiatrix` | female | Gentle |
  | `zephyr` | female | Bright |
  | `zubenelgenubi` | male | Casual |

  **18 female**: achernar, achird, alnilam, aoede, autonoe, callirrhoe, despina, erinome, gacrux, kore, laomedeia, leda, pulcherrima, sadachbia, schedar, sulafat, vindemiatrix, zephyr
  **12 male**: algenib, algieba, charon, enceladus, fenrir, iapetus, orus, puck, rasalgethi, sadaltager, umbriel, zubenelgenubi

  > Source: `ALL_VOICE_PROFILES` in the AI-Parrot platform codebase (authoritative).

- Default `BasicAgent.speakers` (used if YAML does not override):
  ```python
  {
      "interviewer": {"name": "Lydia", "role": "interviewer", "characteristic": "Bright",     "gender": "female"},
      "interviewee": {"name": "Brian", "role": "interviewee", "characteristic": "Informative", "gender": "male"},
  }
  ```
- The TTS model is **always** controlled internally by `BasicAgent.speech_report()` —
  the YAML `llm:` block only affects the script-generation LLM. **Never attempt to pass
  `model` or `temperature` to `speech_report()` for TTS purposes — it will be ignored.**
- `speech_report()` returns exactly:
  ```python
  {
      "script_path": Path,       # always present
      "podcast_path": Path | None,  # None if TTS synthesis failed
  }
  ```
  These two keys map directly to the `script_path` and `podcast_path` DataFrame columns.
  The component does NOT receive the generated script text as a return value.

### Failure Tracking

When `speech_report()` raises any exception for a row:

1. **Log at ERROR level** via `self.logger.error(...)` including title, row index, and full traceback.
2. **Append to JSONL failure log** at `STATIC_DIR/{agent_id}/podcast_failures/failures_<YYYYMMDD>.jsonl`.
   - Create the directory if it does not exist.
   - Each line is a JSON object: `ts`, `title` (from `title_column` or row index), `text_snippet` (first 120 chars), `error` (full exception string), `retry` (always `false` — reserved for future use).
3. **Include the failed row in the output DataFrame** with:
   - `podcast_path = None`
   - `script_path = None`
   - `podcast_error = str(exception)`
4. **Continue processing** remaining rows — do not re-raise.

The JSONL log provides a standalone audit trail: operators can `grep` it or load it with pandas after a run to see exactly which episodes Google TTS rejected and why.

### Known Risks / Gotchas

- **Google TTS rate limits**: Google Cloud TTS frequently returns 429 / 503 errors under load. The JSONL failure log is the primary tool for identifying which episodes need to be re-run. Consider adding a retry wrapper in a future iteration.
- **Speaker config**: `BasicAgent.speakers` defaults (Lydia + Steven) are used unless
  overridden at the agent subclass level. The YAML `llm:` block does not control speakers.
- **Output paths**: `speech_report()` writes to `STATIC_DIR/{agent_id}/podcasts/` and
  `STATIC_DIR/{agent_id}/generated_scripts/` — not to the taskstore. Ensure `STATIC_DIR`
  is writable in the deployment environment. The failure log is also written under `STATIC_DIR`.
- **Context manager**: `speech_report()` must be called inside `async with self._agent:` —
  the current implementation wraps the full row iteration in a single context.

### External Dependencies

| Package | Version | Reason |
|---|---|---|
| `parrot` | current | `BasicAgent`, `AgentBase`, `AgentResponse` |
| `pandas` | `>=2.0` | Input/output DataFrame |

---

## 7. Open Questions

- [x] ~~Use `ParrotBot` or `AgentBase`?~~ — *Resolved: `AgentBase` per Jesus Lara (2026-03-23)*
- [x] ~~Should speakers be configurable per YAML task?~~ — *Resolved: yes, via `self._agent.speakers` override in `start()` (Jesus Lara, 2026-03-23)*
- [x] ~~Should failed episodes produce an error row or be silently skipped?~~ — *Resolved: row kept in output with `podcast_error` populated + appended to JSONL failure log for auditing. Google TTS reliability issues make this essential (juanfran, 2026-03-23)*

---

## Worktree Strategy

- **Isolation unit**: `per-spec` (sequential tasks, single worktree).
- TASK-014 (tests) is the only remaining task — implementation is done.
- No cross-feature dependencies — `ProductReportBot` and `NextStopAgent` are not modified.

---

## Revision History

| Version | Date | Author | Change |
|---|---|---|---|
| 0.1 | 2026-03-23 | juanfran | Initial draft — component implemented with `ParrotBot` |
| 0.2 | 2026-03-23 | juanfran | Rewrite: switched to `AgentBase + BasicAgent.speech_report()` per Jesus Lara review |
| 0.3 | 2026-03-23 | juanfran | Add speaker config via `self._agent.speakers`; clarify TTS model is not configurable from YAML; close open questions; status → approved |
| 0.4 | 2026-03-23 | juanfran | Add failure tracking: failed rows stay in output DataFrame with `podcast_error` column + JSONL audit log per day. Add full TTSVoice enum list. Clarify `speech_report()` return shape (paths only, no script text). Add Google TTS rate-limit gotcha. |
| 0.5 | 2026-03-23 | juanfran | Replace TTSVoice list with full `ALL_VOICE_PROFILES` table from AI-Parrot codebase (authoritative). 30 voices, 18 female / 12 male. Includes Zubenelgenubi (not in public docs). Several gender corrections vs Google public docs (alnilam, schedar, achird, sadachbia). |
