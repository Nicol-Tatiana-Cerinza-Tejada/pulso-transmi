begin;

alter table competition.forecast_cycles
    add column if not exists public_id text,
    add column if not exists data_cutoff timestamptz;

update competition.forecast_cycles
set public_id = 'cyc_' || id::text
where public_id is null;

update competition.forecast_cycles
set data_cutoff = origin_at
where data_cutoff is null;

alter table competition.forecast_cycles
    alter column public_id set not null,
    alter column data_cutoff set not null;

create unique index if not exists forecast_cycles_public_id_key
    on competition.forecast_cycles (public_id);

alter table competition.submissions
    add column if not exists schema_version text not null default '1.0',
    add column if not exists client_run_id text,
    add column if not exists idempotency_key text,
    add column if not exists trained_at timestamptz,
    add column if not exists training_data_end timestamptz,
    add column if not exists request_id text;

create unique index if not exists submissions_participant_idempotency_key
    on competition.submissions (participant_id, idempotency_key)
    where idempotency_key is not null;

create index if not exists submissions_public_receipt_idx
    on competition.submissions (participant_id, public_id);

alter table competition.submissions
    add constraint submissions_schema_version_check
    check (schema_version = '1.0') not valid;
alter table competition.submissions validate constraint submissions_schema_version_check;

alter table competition.submissions
    add constraint submissions_model_version_length_check
    check (char_length(model_version) between 1 and 64) not valid;
alter table competition.submissions validate constraint submissions_model_version_length_check;

alter table competition.submissions
    add constraint submissions_client_run_id_length_check
    check (client_run_id is null or char_length(client_run_id) between 1 and 128) not valid;
alter table competition.submissions validate constraint submissions_client_run_id_length_check;

create or replace view competition.public_scenarios as
select id, code, state, history_start, competition_start, competition_end
from sim.scenarios
where state in ('scheduled', 'running', 'frozen', 'revealed');

revoke all on sim.scenarios, sim.scenario_stations from academy_api;
revoke usage on schema sim from academy_api;
grant select on competition.public_scenarios to academy_api;
grant update on competition.submissions to academy_api;

grant usage on schema ops to academy_api;
grant insert on ops.audit_events to academy_api;
grant usage, select on all sequences in schema ops to academy_api;

insert into ops.schema_migrations (version)
values ('003_submission_protocol')
on conflict (version) do nothing;

commit;
