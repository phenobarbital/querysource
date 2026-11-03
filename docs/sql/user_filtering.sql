-- FEAT-184: per-user automatic filtering columns.
-- Run against the legacy store (QS_QUERIES_SCHEMA.QS_QUERIES_TABLE, default public.queries)
-- and against every tenant <schema>.queries table. Idempotent and ADD-only:
-- h_filtering is kept until 5.3.0 because definition models reject unknown columns.
ALTER TABLE public.queries ADD COLUMN IF NOT EXISTS user_filtering boolean NOT NULL DEFAULT false;
ALTER TABLE public.queries ADD COLUMN IF NOT EXISTS user_filter_options jsonb NOT NULL DEFAULT '{}'::jsonb;

-- Tenant stores: repeat for each schema, e.g.
-- ALTER TABLE epson.queries ADD COLUMN IF NOT EXISTS user_filtering boolean NOT NULL DEFAULT false;
-- ALTER TABLE epson.queries ADD COLUMN IF NOT EXISTS user_filter_options jsonb NOT NULL DEFAULT '{}'::jsonb;

-- 5.3.0 (not part of FEAT-184):
-- ALTER TABLE public.queries DROP COLUMN IF EXISTS h_filtering;
