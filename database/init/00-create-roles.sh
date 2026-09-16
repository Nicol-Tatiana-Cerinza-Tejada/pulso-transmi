#!/bin/sh
set -eu

psql --set=ON_ERROR_STOP=1 \
  --username "$POSTGRES_USER" \
  --dbname "$POSTGRES_DB" \
  --set=api_password="$API_DB_PASSWORD" \
  --set=scheduler_password="$SCHEDULER_DB_PASSWORD" <<'SQL'
select format('create role academy_api login password %L', :'api_password')
where not exists (select 1 from pg_roles where rolname = 'academy_api') \gexec

select format('create role academy_scheduler login password %L', :'scheduler_password')
where not exists (select 1 from pg_roles where rolname = 'academy_scheduler') \gexec

alter role academy_api set statement_timeout = '10s';
alter role academy_scheduler set statement_timeout = '30s';
SQL
