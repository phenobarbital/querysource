-- FEAT-176 query-store move control plane.
--
-- Deploy EXPLICITLY (psql -f docs/sql/query_move_control.sql) before enabling live
-- query moves. QuerySource never runs this DDL at startup or on an HTTP request,
-- and this file never creates or alters any tenant/legacy ``queries`` table.
-- It is repeatable: every statement is IF NOT EXISTS / guarded, so re-running it
-- on an up-to-date database changes nothing.
--
-- Optional least-privilege grants: before running, set the runtime role with
--   SET qs.runtime_role = 'qs_runtime';       -- (or PGOPTIONS='-c qs.runtime_role=...')
-- The grants below never include DDL, TRUNCATE or REFERENCES. No credential,
-- DSN or run-as secret is ever stored in these tables.
--
-- Backfill: schedule state rows are created by the upgraded scheduler during its
-- serialized bootstrap (one row per canonical owner/slug/kind, persisting the
-- existing trigger anchor and cursor). There is nothing to backfill in SQL.

BEGIN;

CREATE SCHEMA IF NOT EXISTS management;

-- ---------------------------------------------------------------------------
-- Durable move journal (one row per operation).
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS management.qs_query_moves (
    operation_id        uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    deployment_id       text        NOT NULL DEFAULT 'default',
    actor_id            bigint      NOT NULL,
    idempotency_key     uuid        NOT NULL,
    request_digest      text        NOT NULL,
    query_slug          text        NOT NULL CHECK (query_slug <> ''),
    -- canonical physical source/target identity (same database only)
    source_namespace    text        NOT NULL,
    source_schema       text        NOT NULL,
    source_table        text        NOT NULL,
    source_contract     text        NOT NULL CHECK (source_contract IN ('legacy', 'tenant')),
    target_namespace    text        NOT NULL,
    target_schema       text        NOT NULL,
    target_table        text        NOT NULL,
    target_contract     text        NOT NULL CHECK (target_contract IN ('legacy', 'tenant')),
    plan_snapshot       jsonb       NOT NULL,
    state               text        NOT NULL DEFAULT 'preparing'
        CHECK (state IN ('preparing', 'draining', 'cutover', 'reconciling', 'completed', 'failed')),
    -- false: row move not committed; true: durable cutover committed;
    -- NULL: acknowledgement indeterminate until journal recovery resolves it.
    moved               boolean,
    scheduler_sync      text        NOT NULL DEFAULT 'pending'
        CHECK (scheduler_sync IN ('pending', 'ok', 'failed', 'not_configured')),
    cutover_at          timestamptz,
    error_code          text,
    message             text,
    lease_owner         text,
    lease_expires_at    timestamptz,
    created_at          timestamptz NOT NULL DEFAULT now(),
    updated_at          timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT qs_query_moves_idempotency UNIQUE (deployment_id, actor_id, idempotency_key),
    CONSTRAINT qs_query_moves_distinct_stores CHECK (
        (source_namespace, source_schema, source_table)
        IS DISTINCT FROM (target_namespace, target_schema, target_table)
    ),
    CONSTRAINT qs_query_moves_completed_is_moved CHECK (state <> 'completed' OR moved IS TRUE),
    CONSTRAINT qs_query_moves_failed_not_moved CHECK (state <> 'failed' OR moved IS FALSE)
);

CREATE INDEX IF NOT EXISTS qs_query_moves_recovery_idx
    ON management.qs_query_moves (state, lease_expires_at)
    WHERE state NOT IN ('completed', 'failed');

-- ---------------------------------------------------------------------------
-- Canonical identity reservations: an unfinished operation holds BOTH its
-- source and target identity. The primary key makes overlapping, opposite-
-- direction or duplicate moves collide in the database, not just in code.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS management.qs_move_reservations (
    namespace_id    text        NOT NULL,
    schema_name     text        NOT NULL,
    table_name      text        NOT NULL,
    query_slug      text        NOT NULL,
    operation_id    uuid        NOT NULL
        REFERENCES management.qs_query_moves (operation_id) ON DELETE CASCADE,
    role            text        NOT NULL CHECK (role IN ('source', 'target')),
    created_at      timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (namespace_id, schema_name, table_name, query_slug)
);

CREATE INDEX IF NOT EXISTS qs_move_reservations_operation_idx
    ON management.qs_move_reservations (operation_id);

-- ---------------------------------------------------------------------------
-- Shared logical schedule state (one row per canonical owner/slug/kind).
-- The logical id survives an owner change; generation fences stale mirrors.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS management.qs_schedule_state (
    logical_schedule_id uuid        PRIMARY KEY DEFAULT gen_random_uuid(),
    owner_namespace     text        NOT NULL,
    owner_schema        text        NOT NULL,
    owner_table         text        NOT NULL,
    query_slug          text        NOT NULL CHECK (query_slug <> ''),
    kind                text        NOT NULL CHECK (kind IN ('query', 'multi', 'cache')),
    generation          bigint      NOT NULL DEFAULT 1 CHECK (generation >= 1),
    config_revision     bigint      NOT NULL DEFAULT 1 CHECK (config_revision >= 1),
    trigger_spec        jsonb       NOT NULL,
    timezone            text        NOT NULL DEFAULT 'UTC',
    anchor_at           timestamptz,
    -- next UNCLAIMED fire time (advanced atomically by admission) and the last claimed slot
    next_fire_at        timestamptz,
    last_claimed_fire_at timestamptz,
    paused              boolean     NOT NULL DEFAULT false,
    enabled             boolean     NOT NULL DEFAULT true,
    dispatch_gate       text        NOT NULL DEFAULT 'open' CHECK (dispatch_gate IN ('open', 'frozen')),
    gate_operation_id   uuid REFERENCES management.qs_query_moves (operation_id) ON DELETE SET NULL,
    max_instances       integer     NOT NULL DEFAULT 1 CHECK (max_instances >= 1),
    coalesce_missed     boolean     NOT NULL DEFAULT true,
    misfire_grace_seconds integer   CHECK (misfire_grace_seconds IS NULL OR misfire_grace_seconds >= 0),
    created_at          timestamptz NOT NULL DEFAULT now(),
    updated_at          timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT qs_schedule_state_owner_unique
        UNIQUE (owner_namespace, owner_schema, owner_table, query_slug, kind)
);

CREATE INDEX IF NOT EXISTS qs_schedule_state_due_idx
    ON management.qs_schedule_state (next_fire_at)
    WHERE enabled AND NOT paused;

-- ---------------------------------------------------------------------------
-- One row per admitted occurrence/attempt. (logical_schedule_id, scheduled_fire_at)
-- is unique, so any number of processes waking for the same slot admit it once.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS management.qs_schedule_runs (
    run_id              uuid        PRIMARY KEY DEFAULT gen_random_uuid(),
    logical_schedule_id uuid        NOT NULL
        REFERENCES management.qs_schedule_state (logical_schedule_id) ON DELETE CASCADE,
    generation          bigint      NOT NULL,
    scheduled_fire_at   timestamptz NOT NULL,
    owner_namespace     text        NOT NULL,
    owner_schema        text        NOT NULL,
    owner_table         text        NOT NULL,
    query_slug          text        NOT NULL,
    worker_id           text,
    outcome             text        NOT NULL DEFAULT 'admitted'
        CHECK (outcome IN ('admitted', 'running', 'completed', 'failed', 'unknown')),
    error               text,
    admitted_at         timestamptz NOT NULL DEFAULT now(),
    finished_at         timestamptz,
    CONSTRAINT qs_schedule_runs_occurrence UNIQUE (logical_schedule_id, scheduled_fire_at)
);

CREATE INDEX IF NOT EXISTS qs_schedule_runs_open_idx
    ON management.qs_schedule_runs (logical_schedule_id)
    WHERE outcome IN ('admitted', 'running', 'unknown');

-- ---------------------------------------------------------------------------
-- Scheduler worker registry (protocol readiness; heartbeats are advisory only).
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS management.qs_scheduler_workers (
    worker_id           text        PRIMARY KEY,
    protocol_version    integer     NOT NULL CHECK (protocol_version >= 1),
    process_id          integer,
    heartbeat_at        timestamptz NOT NULL DEFAULT now(),
    started_at          timestamptz NOT NULL DEFAULT now()
);

-- ---------------------------------------------------------------------------
-- Least-privilege grants (only when qs.runtime_role is set and the role exists).
-- ---------------------------------------------------------------------------
DO $grants$
DECLARE
    runtime_role text := nullif(current_setting('qs.runtime_role', true), '');
BEGIN
    IF runtime_role IS NOT NULL AND EXISTS (SELECT 1 FROM pg_roles WHERE rolname = runtime_role) THEN
        EXECUTE format('GRANT USAGE ON SCHEMA management TO %I', runtime_role);
        EXECUTE format(
            'GRANT SELECT, INSERT, UPDATE ON management.qs_query_moves, '
            'management.qs_schedule_state, management.qs_schedule_runs, '
            'management.qs_scheduler_workers TO %I', runtime_role);
        EXECUTE format(
            'GRANT SELECT, INSERT, DELETE ON management.qs_move_reservations TO %I', runtime_role);
        EXECUTE format(
            'GRANT DELETE ON management.qs_scheduler_workers, management.qs_schedule_state TO %I',
            runtime_role);
    END IF;
END
$grants$;

COMMIT;
