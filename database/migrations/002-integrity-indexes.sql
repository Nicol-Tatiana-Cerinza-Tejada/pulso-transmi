begin;

create table if not exists ops.schema_migrations (
    version text primary key,
    applied_at timestamptz not null default now()
);

insert into ops.schema_migrations (version)
values ('001_initial_schema')
on conflict (version) do nothing;

create index if not exists scenarios_generator_version_idx
    on sim.scenarios (generator_version_id);
create index if not exists validation_runs_scenario_idx
    on sim.validation_runs (scenario_id, started_at desc);
create index if not exists api_keys_participant_idx
    on competition.api_keys (participant_id);
create index if not exists submissions_cycle_idx
    on competition.submissions (cycle_id, received_at desc);
create index if not exists predictions_station_idx
    on competition.predictions (station_id, target_at);
create index if not exists cycle_entries_participant_idx
    on competition.cycle_entries (participant_id, cycle_id);
create index if not exists score_snapshots_participant_idx
    on competition.score_snapshots (participant_id, calculated_at desc);

do $$
begin
    if not exists (
        select 1 from pg_constraint
        where conname = 'submissions_id_participant_cycle_key'
          and conrelid = 'competition.submissions'::regclass
    ) then
        alter table competition.submissions
            add constraint submissions_id_participant_cycle_key
            unique (id, participant_id, cycle_id);
    end if;
end $$;

alter table competition.cycle_entries
    drop constraint if exists cycle_entries_official_submission_id_fkey;

do $$
begin
    if not exists (
        select 1 from pg_constraint
        where conname = 'cycle_entries_official_submission_owner_fkey'
          and conrelid = 'competition.cycle_entries'::regclass
    ) then
        alter table competition.cycle_entries
            add constraint cycle_entries_official_submission_owner_fkey
            foreign key (official_submission_id, participant_id, cycle_id)
            references competition.submissions(id, participant_id, cycle_id);
    end if;
end $$;

alter table competition.score_components
    drop constraint if exists score_components_submission_id_fkey;

do $$
begin
    if not exists (
        select 1 from pg_constraint
        where conname = 'score_components_cycle_target_fkey'
          and conrelid = 'competition.score_components'::regclass
    ) then
        alter table competition.score_components
            add constraint score_components_cycle_target_fkey
            foreign key (cycle_id, station_id, target_at)
            references competition.cycle_targets(cycle_id, station_id, target_at);
    end if;

    if not exists (
        select 1 from pg_constraint
        where conname = 'score_components_submission_owner_fkey'
          and conrelid = 'competition.score_components'::regclass
    ) then
        alter table competition.score_components
            add constraint score_components_submission_owner_fkey
            foreign key (submission_id, participant_id, cycle_id)
            references competition.submissions(id, participant_id, cycle_id);
    end if;
end $$;

alter table competition.predictions
    drop constraint if exists predictions_predicted_value_check;
alter table competition.predictions
    add constraint predictions_predicted_value_check check (
        predicted_value >= 0
        and predicted_value <> 'NaN'::float8
        and predicted_value not in ('Infinity'::float8, '-Infinity'::float8)
    );

insert into ops.schema_migrations (version)
values ('002_integrity_indexes')
on conflict (version) do nothing;

commit;
