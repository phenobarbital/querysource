# SDD: Duplicate Task Execution ID Detected in Scheduler/workers and influx Logs (NAV-6365)

**Status**: Draft  
**Reference**: [NAV-6365](https://trocglobal.atlassian.net/browse/NAV-6365)  
**Date**: 2026-03-02  

## 1. Motivation & Business Requirements
Ensuring that every task execution has a unique and persistent identifier is critical for:
- **Auditability**: Tracking the exact history of a specific run.
- **Analytics & Observability**: Aggregating logs and metrics (InfluxDB) without collisions.
- **Error Resolution**: Identifying specific failed attempts vs. retries.

The current duplication violates the core expectation of unique execution tracking.

## 2. Problem Description
Multiple executions of the same task are incorrectly using the same `execution_id` in the `stats` payload. This has been specifically observed in production for tasks such as `hisense.photos`, `bose.tickets`, and `networkninja.form_data`.

### Impact
- Conflicts in InfluxDB/stats logs.
- Difficulty in debugging specific task instances.
- Potential race conditions or data overwrites in down-stream processing if the ID is used as a key.

## 3. Technical Analysis
Research indicates that the root cause lies in the interaction between the **flowtask scheduler** and the **qworker service**.

### Findings
- **Scheduler Dispatch**: When `flowtask` scheduler dispatches a task automatically, it appears to reuse a cached or stale UUID in some scenarios.
- **Manual vs. Automated**: Tasks triggered manually (via task monitor) generate a fresh ID correctly.
- **Versions**: Fixes were attempted in `flowtask` (5.8.22) and `qworker` (1.13.1), but the issue persists in the production cluster for automated dispatches.

## 4. Proposed Solution
The solution requires ensuring that a new UUID is generated at the moment of dispatch by the scheduler.

### Steps
1. **Scheduler Logic Review**: Audit the task creation/dispatch loop in `flowtask` to ensure the `execution_id` is NOT part of the persistent task definition but is generated per-run.
2. **UUID Generation**: Explicitly call a UUID generator immediately before pushing the task to the queue.
3. **Payload Inspection**: Ensure the `stats` payload in both `qworker` and `influx` logs is strictly using the newly generated `execution_id`.
4. **Integration Testing**: Implement a test case that simulates multiple automated triggers to verify ID uniqueness.

## 5. Metadata & Tracking
- **Assignee**: Jesus Lara
- **Reporter**: Jhoanir Torres
- **Linked Issues**: NVP-177
