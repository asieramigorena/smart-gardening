-- Run by the db-roles service (infra/server/docker-compose.yml) after the migrations, on every
-- deploy, so the role passwords always match SSM. Values come in as psql variables.
\set ON_ERROR_STOP on
ALTER ROLE app WITH PASSWORD :'app_password';
ALTER ROLE grafana WITH PASSWORD :'grafana_password';
