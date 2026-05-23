---
id: F005
query: Status of FEAT-093 (ThreadSource) and FEAT-094 (AbstractDestination)
type: read
---

## FEAT-093: multiquery-new-sources (in worktree)
- Adds `ThreadSource(threading.Thread, ABC)` base class
- Adds SourceSharepoint, SourceSmartSheet, SourceS3, SourceTable
- Status: approved, tasks TASK-644–652 in sdd/tasks/active/
- Worktree: `.claude/worktrees/feat-093-multiquery-new-sources/`
- NOT merged to dev yet

## FEAT-094: multiquery-destinations (in worktree)
- Adds `AbstractDestination(ABC)` base class
- Adds ToSharepoint, ToS3, TableDestination, DWHDestination
- Has `DESTINATION_REGISTRY` dict for discovery
- Status: approved, tasks TASK-653–659 in sdd/tasks/active/
- Worktree: `.claude/worktrees/feat-094-multiquery-destinations/`
- NOT merged to dev yet

## Impact on FEAT-095
User explicitly states: "ThreadSource and AbstractDestination will be deferred
to next feature because ThreadSource is currently be developed by FEAT-093."
FEAT-095 should focus on AbstractTransform, AbstractOperator, AbstractComponent
only. ThreadSource and AbstractDestination integration comes later.
