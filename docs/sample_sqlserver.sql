--- SELECT iata, city, country FROM dbo.airports
--- raw SQL datasource:
{
 "driver": "sqlserver",
 "host": "localhost",
 "port": 1433,
 "username": "sa",
 "password": "P4ssW0rd1.",
 "database": "AdventureWorks2019"
}

{ "username": "sa", "password": "P4ssW0rd1." }

--- Create a sample slug:
INSERT INTO public.queries(query_slug, query_raw, program_id, program_slug, is_cached, dwh, provider, raw_query)
VALUES ('sample_airports', 'SELECT iata, city, country FROM dbo.airports', 1, 'default', false, false, 'sqlserver', true);
--- using default connector:
INSERT INTO public.queries(query_slug, query_raw, program_id, program_slug, is_cached, dwh, provider, raw_query)
VALUES ('sample_airports_default', 'SELECT iata, city, country FROM dbo.airports', 1, 'default', false, false, 'sqlserver', true);
--- using Parser:
INSERT INTO public.queries(query_slug, query_raw, program_id, program_slug, is_cached, dwh, provider, raw_query)
VALUES ('sample_persons', 'SELECT {fields} FROM "Person"."Person" {filter}', 1, 'default', false, false, 'sqlserver', false);
--- vibaContent
INSERT INTO public.queries(query_slug, query_raw, program_id, program_slug, is_cached, dwh, provider, raw_query)
VALUES ('viba_content', 'SELECT {fields} FROM "VibaContent" {filter}', 1, 'default', false, false, 'sqlserver', false) ON CONFLICT (query_slug) DO UPDATE SET query_raw = EXCLUDED.query_raw;
