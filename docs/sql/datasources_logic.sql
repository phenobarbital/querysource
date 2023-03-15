--- Model for Datasource Logic:
--- v1 12-09-2022: first version

DROP TABLE IF EXISTS public.datasources;
CREATE TABLE public.datasources
(
  uid uuid NOT NULL DEFAULT uuid_generate_v4(),
  driver character varying(20),
  name character varying(90),
  description character varying(250),
  params jsonb,
  credentials jsonb,
  dsn character varying(512),
  program_slug character varying(60),
  drv text,
  created_at timestamp with time zone NOT NULL DEFAULT now(),
  updated_at timestamp with time zone,
  CONSTRAINT qs_datasources_type_pkey PRIMARY KEY (uid),
  CONSTRAINT unq_qs_datasource_name UNIQUE (name)
)
WITH (
  OIDS=FALSE
);

---- DEFAULT
INSERT INTO public.datasources (name, driver, description, params, credentials, program_slug)
VALUES('default', 'pg', 'PostgreSQL Default Database', '{"host": "127.0.0.1", "port": 5432, "database": "qs_dev"}', '{"password": "DBPWD", "user": "DBUSER"}', 'default')
ON CONFLICT (name) DO NOTHING;

--- My Local SQLServer
INSERT INTO public.datasources (name, driver, description, params, credentials, program_slug)
VALUES('localsql', 'sqlserver', 'MS SQL Server Local', '{"host": "localhost", "port": 1433, "database": "AdventureWorks2019"}', '{"password": "P4ssW0rd1.", "user": "sa"}', 'default')
ON CONFLICT (name) DO NOTHING;

--- TEST using DSN
INSERT INTO public.datasources (name, driver, description, params, credentials, dsn, program_slug)
VALUES('testpg', 'pg', 'PostgresSQL DEV (dsn)', null, null, 'postgres://qsuser:12345678@localhost:5432/qs_dev', 'default')
ON CONFLICT (name) DO NOTHING;
