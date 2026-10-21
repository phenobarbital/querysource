# TASK-934: Failure alert callback (async-notify)

**Feature**: FEAT-181 — Scheduler Execution History
**Spec**: `sdd/specs/scheduler-execution-history.spec.md`
**Status**: pending
**Priority**: medium
**Estimated effort**: M (2-4h)
**Depends-on**: TASK-929
**Assigned-to**: unassigned

---

## Context

Spec §3 Module 7. Failures are silent today (WARNING only). This task adds an
async-notify callback for `NotificationManager`. Only the claimer reaches
`notify()`, so it sends one alert per failed occurrence. The provider is
configurable (default Telegram, flowtask precedent) with a per-job override.

---

## Scope

- Add `AlertCallback` to `querysource/scheduler/notifications.py`:
  - a sync `__call__(job_id, slug, error)` that schedules an async send on the
    running loop and never raises;
  - provider resolution: per-job map, then `QS_SCHEDULER_NOTIFY`; `none` disables
    sending;
  - telegram via `EVENT_CHAT_BOT`/`EVENT_CHAT_ID`;
  - message format per spec;
  - `warn_history_unavailable(job_id, slug)`, rate-limited by
    `QS_SCHEDULER_ALERT_COOLDOWN`.
- Tests with a fake `Notify`.

**NOT in scope**: registering the callback in the scheduler (TASK-937).

---

## Files to Create / Modify

| File | Action | Description |
|---|---|---|
| `querysource/scheduler/notifications.py` | MODIFY | Add `AlertCallback` |
| `tests/scheduler/test_alerts.py` | CREATE | Provider/override/cooldown/error tests |

---

## Codebase Contract (Anti-Hallucination)

### Verified Imports
```python
from navconfig.logging import logging                                  # verified: querysource/scheduler/notifications.py:10
from querysource.conf import (ENVIRONMENT, QS_SCHEDULER_NOTIFY, QS_SCHEDULER_ALERT_COOLDOWN,
                              QS_SCHEDULER_ALERT_URL, EVENT_CHAT_BOT, EVENT_CHAT_ID)   # conf.py:214 + TASK-929
# Lazy inside the send coroutine (async-notify: 2.0.0 in uv.lock, 1.6.0 on prod):
from notify import Notify                       # Notify(provider: str, *args, **kwargs) — verified signature
from notify.models import Chat                  # flowtask 1.6.x usage: Chat(chat_id=..., chat_name=...)
```

### Existing Signatures to Use
```python
# querysource/scheduler/notifications.py
def logging_callback(job_id: str, slug: str, error: Exception) -> None      # line 14
class NotificationManager:                                                  # line 21
    def notify(self, job_id: str, slug: str, error: Exception) -> None      # line 37 — calls callback(job_id=, slug=, error=)
# flowtask/scheduler/notifications.py (reference only): async with ntf as conn: await conn.send(recipient=[chat], message=msg)
```

### Does NOT Exist
- ~~Any `notify` import in `querysource/` today~~: this is the first use. Keep it lazy.
- ~~`DEFAULT_RECIPIENT` in querysource conf~~: email recipient support is FILL IN behind a config key; skip it if unset.

---

## Complexity Contract

```json
{
  "schema_version": 1,
  "targets": [
    {"path": "querysource/scheduler/notifications.py", "action": "MODIFY"},
    {"path": "tests/scheduler/test_alerts.py", "action": "CREATE"}
  ],
  "contract_symbols": ["sym:querysource/scheduler/notifications.py#NotificationManager"]
}
```

---

## Implementation Blueprint

### Steps (in order)
1. Insert `AlertCallback` above `class NotificationManager`. *Why:* the module
   reads top-down.
2. Use only `Notify(provider, **kwargs)` + `async with … as conn: await conn.send(recipient=…, message=…)`.
   *Why:* it is the API surface common to async-notify 1.6 (prod) and 2.0 (lock).
3. Schedule with `asyncio.get_running_loop().create_task(...)`; if there is no running
   loop, log and return. *Why:* `notify()` is sync and must stay sync.

### `querysource/scheduler/notifications.py` (MODIFY)
```python
# occurrences: 1 (verified: grep -c 'class NotificationManager:' querysource/scheduler/notifications.py)
# BEFORE `class NotificationManager:` (verified: querysource/scheduler/notifications.py:21)
class AlertCallback:
    """async-notify failure alerts; sync entry point, async send, never raises."""

    def __init__(self, provider: str = QS_SCHEDULER_NOTIFY, *, per_job: Mapping[str, str] | None = None,
                 environment: str = ENVIRONMENT, link: str | None = QS_SCHEDULER_ALERT_URL) -> None:
        self.provider = (provider or "none").lower()
        self.per_job: dict[str, str] = dict(per_job or {})
        self.environment = environment
        self.link = link
        self._last_warning: float = 0.0

    def __call__(self, job_id: str, slug: str, error: Exception) -> None:
        """Schedule a failure alert for job_id; never raises."""
        message = f"🛑 [{self.environment} QSScheduler] {slug} ({job_id}) failed: {str(error)[:500]}"
        self._schedule(self.per_job.get(job_id, self.provider), message)

    def warn_history_unavailable(self, job_id: str, slug: str) -> None:
        """Rate-limited ⚠️ alert used by the fail-open path."""
        # FILL IN: honour QS_SCHEDULER_ALERT_COOLDOWN via time.monotonic(); message "⚠️ … history unavailable, ran untracked"
        raise NotImplementedError

    def _schedule(self, provider: str, message: str) -> None:
        # FILL IN: return when provider == "none"; append self.link when set; create_task(self._send(provider, message))
        #   inside try/except RuntimeError (no running loop) → logger.warning
        raise NotImplementedError

    async def _send(self, provider: str, message: str) -> None:
        # FILL IN: lazy `from notify import Notify`; telegram → Notify("telegram", bot_token=EVENT_CHAT_BOT) and
        #   Chat(chat_id=EVENT_CHAT_ID, chat_name="Navigator"); skip with WARNING when credentials are unset;
        #   any exception → logger.exception, never raise
        raise NotImplementedError
```
Add `import asyncio`, `import time` and `from collections.abc import Mapping` to the
existing imports, plus the conf import above.

### FILL IN checklist
- [ ] Cooldown logic; provider `none`; missing credentials; send error swallowed.
- [ ] Tests with a monkeypatched `notify` module.

---

## Acceptance Criteria

- [ ] `AlertCallback()(job_id, slug, exc)` returns immediately and never raises.
- [ ] A per-job override wins over `QS_SCHEDULER_NOTIFY`; `none` sends nothing.
- [ ] `warn_history_unavailable` sends at most once per cooldown.
- [ ] `notify` stays importable without async-notify configured.

---

## Validation Commands

- `pytest tests/scheduler/test_alerts.py -q`

---

## Agent Instructions

1. Work in the feature worktree; check TASK-929 is done.
2. Set `in-progress`; implement; complete every `# FILL IN:`; run the Validation Commands.
3. Close with `scripts/sdd/close_task.sh TASK-934 scheduler-execution-history verified`.

---

## Completion Note

*(Agent fills this in when done)*
