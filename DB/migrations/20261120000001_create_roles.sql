-- Login roles used by the services. Passwords are NOT set here (migrations are public): the
-- db-roles step in infra/server/docker-compose.yml sets them on every deploy from the values
-- that fetch-secrets.sh reads from SSM.
--
-- Migrations run as the superuser (postgres), so every table they create is owned by postgres.
-- The default privileges below give app and grafana access to those future tables too.

-- migrate:up
DO $$
BEGIN
  IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'app') THEN
    CREATE ROLE app LOGIN;
  END IF;
  IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'grafana') THEN
    CREATE ROLE grafana LOGIN;
  END IF;
END
$$;

-- Grafana can only read, even if someone grants it more by mistake.
ALTER ROLE grafana SET default_transaction_read_only = on;

DO $$
BEGIN
  EXECUTE format('GRANT CONNECT ON DATABASE %I TO app, grafana', current_database());
END
$$;

GRANT USAGE ON SCHEMA public TO app, grafana;

-- app: read/write on every table created by migrations from now on.
ALTER DEFAULT PRIVILEGES FOR ROLE postgres IN SCHEMA public
  GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO app;
ALTER DEFAULT PRIVILEGES FOR ROLE postgres IN SCHEMA public
  GRANT USAGE, SELECT ON SEQUENCES TO app;

-- grafana: SELECT only, on every future table.
ALTER DEFAULT PRIVILEGES FOR ROLE postgres IN SCHEMA public
  GRANT SELECT ON TABLES TO grafana;

-- migrate:down
ALTER DEFAULT PRIVILEGES FOR ROLE postgres IN SCHEMA public
  REVOKE SELECT ON TABLES FROM grafana;
ALTER DEFAULT PRIVILEGES FOR ROLE postgres IN SCHEMA public
  REVOKE USAGE, SELECT ON SEQUENCES FROM app;
ALTER DEFAULT PRIVILEGES FOR ROLE postgres IN SCHEMA public
  REVOKE SELECT, INSERT, UPDATE, DELETE ON TABLES FROM app;
REVOKE USAGE ON SCHEMA public FROM app, grafana;
DO $$
BEGIN
  EXECUTE format('REVOKE CONNECT ON DATABASE %I FROM app, grafana', current_database());
END
$$;
DROP ROLE IF EXISTS grafana;
DROP ROLE IF EXISTS app;
