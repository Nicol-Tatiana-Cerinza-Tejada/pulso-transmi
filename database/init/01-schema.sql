begin;

revoke create on schema public from public;

create schema if not exists catalog;
create schema if not exists sim;
create schema if not exists competition;
create schema if not exists ops;

create table ops.schema_migrations (
    version text primary key,
    applied_at timestamptz not null default now()
);

insert into ops.schema_migrations (version) values ('001_initial_schema');

create table catalog.stations (
    station_id text primary key,
    name text not null check (btrim(name) <> ''),
    latitude double precision not null check (latitude between 4.0 and 5.0),
    longitude double precision not null check (longitude between -75.0 and -73.0),
    source_metadata jsonb not null default '{}'::jsonb,
    active boolean not null default true,
    created_at timestamptz not null default now()
);

create table sim.generator_versions (
    id bigint generated always as identity primary key,
    version text not null unique,
    git_commit text not null,
    container_digest text,
    config_schema_version integer not null check (config_schema_version > 0),
    created_at timestamptz not null default now()
);

create table sim.scenarios (
    id bigint generated always as identity primary key,
    code text not null unique,
    generator_version_id bigint not null references sim.generator_versions(id),
    state text not null default 'draft' check (
        state in ('draft', 'generated', 'validated', 'scheduled', 'running', 'frozen', 'revealed', 'cancelled')
    ),
    history_start timestamptz not null,
    competition_start timestamptz not null,
    competition_end timestamptz not null,
    seed_ciphertext bytea not null,
    config jsonb not null,
    config_hash text not null,
    generated_at timestamptz,
    validated_at timestamptz,
    frozen_at timestamptz,
    created_at timestamptz not null default now(),
    check (history_start < competition_start),
    check (competition_start < competition_end)
);

create index scenarios_generator_version_idx
    on sim.scenarios (generator_version_id);

create table sim.scenario_stations (
    scenario_id bigint not null references sim.scenarios(id) on delete cascade,
    station_id text not null references catalog.stations(station_id),
    display_order smallint not null check (display_order > 0),
    is_benchmark boolean not null default true,
    archetype text not null check (
        archetype in ('residential', 'business', 'interchange', 'university', 'leisure')
    ),
    parameters jsonb not null,
    primary key (scenario_id, station_id),
    unique (scenario_id, display_order)
);

create index scenario_stations_station_idx on sim.scenario_stations (station_id);

create table sim.station_edges (
    scenario_id bigint not null,
    source_station_id text not null,
    target_station_id text not null,
    weight double precision not null check (weight between 0 and 1),
    lag_steps smallint not null default 1 check (lag_steps between 1 and 8),
    redistribution_weight double precision not null default 0 check (redistribution_weight between 0 and 1),
    primary key (scenario_id, source_station_id, target_station_id),
    foreign key (scenario_id, source_station_id)
        references sim.scenario_stations(scenario_id, station_id) on delete cascade,
    foreign key (scenario_id, target_station_id)
        references sim.scenario_stations(scenario_id, station_id) on delete cascade,
    check (source_station_id <> target_station_id)
);

create index station_edges_target_idx
    on sim.station_edges (scenario_id, target_station_id);

create table sim.time_context (
    scenario_id bigint not null references sim.scenarios(id) on delete cascade,
    observed_at timestamptz not null,
    is_holiday boolean not null default false,
    rain_actual double precision not null check (rain_actual >= 0),
    rain_forecast double precision not null check (rain_forecast >= 0),
    temperature_actual double precision not null,
    temperature_forecast double precision not null,
    city_latent_factor double precision not null,
    private_context jsonb not null default '{}'::jsonb,
    public_context jsonb not null default '{}'::jsonb,
    primary key (scenario_id, observed_at)
);

create table sim.events (
    id bigint generated always as identity primary key,
    scenario_id bigint not null references sim.scenarios(id) on delete cascade,
    event_type text not null,
    latitude double precision not null,
    longitude double precision not null,
    starts_at timestamptz not null,
    ends_at timestamptz not null,
    announced_at timestamptz not null,
    intensity double precision not null check (intensity >= 0),
    radius_km double precision not null check (radius_km > 0),
    parameters jsonb not null default '{}'::jsonb,
    check (starts_at < ends_at),
    check (announced_at <= starts_at)
);

create index events_scenario_time_idx on sim.events (scenario_id, starts_at, ends_at);

create table sim.drift_events (
    id bigint generated always as identity primary key,
    scenario_id bigint not null references sim.scenarios(id) on delete cascade,
    drift_type text not null check (
        drift_type in ('level_shift', 'peak_shift', 'trend_change', 'closure', 'weather_change', 'event_regime', 'weekend_change', 'variance_shift')
    ),
    starts_at timestamptz not null,
    ends_at timestamptz,
    transition_minutes integer not null default 0 check (transition_minutes >= 0),
    parameters jsonb not null,
    reveal_at timestamptz,
    check (ends_at is null or starts_at < ends_at)
);

create index drift_events_scenario_time_idx on sim.drift_events (scenario_id, starts_at);

create table sim.drift_targets (
    drift_event_id bigint not null references sim.drift_events(id) on delete cascade,
    station_id text not null references catalog.stations(station_id),
    weight double precision not null default 1 check (weight between 0 and 1),
    primary key (drift_event_id, station_id)
);

create index drift_targets_station_idx on sim.drift_targets (station_id);

create table sim.generated_truth (
    scenario_id bigint not null,
    station_id text not null,
    observed_at timestamptz not null,
    expected_mean double precision not null check (expected_mean >= 0),
    actual_value integer not null check (actual_value >= 0),
    seasonal_component double precision not null,
    calendar_component double precision not null,
    weather_component double precision not null,
    event_component double precision not null,
    spatial_component double precision not null,
    drift_component double precision not null,
    latent_component double precision not null,
    generated_at timestamptz not null default now(),
    primary key (scenario_id, station_id, observed_at),
    foreign key (scenario_id, station_id)
        references sim.scenario_stations(scenario_id, station_id) on delete cascade
);

create index generated_truth_time_idx
    on sim.generated_truth (scenario_id, observed_at, station_id);

create table sim.validation_runs (
    id bigint generated always as identity primary key,
    scenario_id bigint not null references sim.scenarios(id) on delete cascade,
    started_at timestamptz not null default now(),
    completed_at timestamptz,
    status text not null check (status in ('running', 'passed', 'failed')),
    checks jsonb not null default '{}'::jsonb,
    error_message text
);

create index validation_runs_scenario_idx
    on sim.validation_runs (scenario_id, started_at desc);

create table competition.scenario_clock (
    scenario_id bigint primary key references sim.scenarios(id) on delete cascade,
    virtual_now timestamptz not null,
    tick_number bigint not null default 0 check (tick_number >= 0),
    last_tick_at timestamptz,
    state text not null default 'paused' check (state in ('paused', 'running', 'completed')),
    version bigint not null default 0
);

create table competition.participants (
    id bigint generated always as identity primary key,
    public_id text not null unique,
    kind text not null default 'student' check (kind in ('student', 'baseline', 'admin')),
    display_name text not null check (btrim(display_name) <> ''),
    slug text not null unique,
    eligible boolean not null default true,
    cohort_code text,
    section_code text,
    login_name_hash bytea,
    login_email_hash bytea,
    login_student_code_hash bytea,
    credential_claimed_at timestamptz,
    created_at timestamptz not null default now()
);

create unique index participants_login_email_hash_key
    on competition.participants (login_email_hash)
    where login_email_hash is not null;

create unique index participants_login_student_code_hash_key
    on competition.participants (login_student_code_hash)
    where login_student_code_hash is not null;

create table competition.participant_scenarios (
    participant_id bigint not null references competition.participants(id) on delete cascade,
    scenario_id bigint not null references sim.scenarios(id) on delete cascade,
    status text not null default 'active' check (status in ('active', 'suspended', 'finished')),
    joined_at timestamptz not null default now(),
    primary key (participant_id, scenario_id)
);

create index participant_scenarios_scenario_idx
    on competition.participant_scenarios (scenario_id, status);

create table competition.api_keys (
    id bigint generated always as identity primary key,
    participant_id bigint not null references competition.participants(id) on delete cascade,
    key_prefix text not null unique,
    secret_hash text not null,
    created_at timestamptz not null default now(),
    last_used_at timestamptz,
    revoked_at timestamptz
);

create index api_keys_participant_idx
    on competition.api_keys (participant_id);

create index api_keys_active_participant_idx
    on competition.api_keys (participant_id)
    where revoked_at is null;

create table competition.portal_sessions (
    token_hash text primary key,
    participant_id bigint not null references competition.participants(id) on delete cascade,
    preferred_name text check (
        preferred_name is null or char_length(btrim(preferred_name)) between 1 and 160
    ),
    created_at timestamptz not null default now(),
    expires_at timestamptz not null,
    last_seen_at timestamptz not null default now(),
    revoked_at timestamptz,
    check (created_at < expires_at)
);

create index portal_sessions_active_participant_idx
    on competition.portal_sessions (participant_id, expires_at desc)
    where revoked_at is null;

create table competition.observations (
    scenario_id bigint not null,
    station_id text not null,
    observed_at timestamptz not null,
    value integer not null check (value >= 0),
    released_at timestamptz not null default now(),
    quality_flag text not null default 'ok' check (quality_flag in ('ok', 'corrected', 'late')),
    primary key (scenario_id, station_id, observed_at),
    foreign key (scenario_id, station_id)
        references sim.scenario_stations(scenario_id, station_id)
);

create index observations_cursor_idx
    on competition.observations (scenario_id, released_at, station_id);

create table competition.public_time_context (
    scenario_id bigint not null references sim.scenarios(id) on delete cascade,
    observed_at timestamptz not null,
    rain_observed double precision,
    rain_forecast double precision not null,
    temperature_observed double precision,
    temperature_forecast double precision not null,
    is_holiday boolean not null default false,
    released_at timestamptz not null default now(),
    primary key (scenario_id, observed_at)
);

create table competition.public_events (
    id bigint generated always as identity primary key,
    scenario_id bigint not null references sim.scenarios(id) on delete cascade,
    event_code text not null,
    event_type text not null,
    latitude double precision not null,
    longitude double precision not null,
    starts_at timestamptz not null,
    ends_at timestamptz not null,
    size_band text not null,
    released_at timestamptz not null default now(),
    unique (scenario_id, event_code)
);

create index public_events_scenario_time_idx
    on competition.public_events (scenario_id, starts_at, ends_at);

create table competition.forecast_cycles (
    id bigint generated always as identity primary key,
    public_id text not null unique,
    scenario_id bigint not null references sim.scenarios(id) on delete cascade,
    origin_at timestamptz not null,
    data_cutoff timestamptz not null,
    opens_at timestamptz not null,
    closes_at timestamptz not null,
    target_start_at timestamptz not null,
    target_end_at timestamptz not null,
    state text not null default 'scheduled' check (state in ('scheduled', 'open', 'closed', 'resolved', 'cancelled')),
    unique (scenario_id, origin_at),
    check (opens_at < closes_at),
    check (target_start_at <= target_end_at)
);

create index forecast_cycles_state_idx
    on competition.forecast_cycles (scenario_id, state, opens_at);

create table competition.cycle_targets (
    cycle_id bigint not null references competition.forecast_cycles(id) on delete cascade,
    station_id text not null references catalog.stations(station_id),
    target_at timestamptz not null,
    horizon_steps smallint not null check (horizon_steps between 1 and 4),
    primary key (cycle_id, station_id, target_at)
);

create index cycle_targets_station_time_idx
    on competition.cycle_targets (station_id, target_at);

create table competition.submissions (
    id bigint generated always as identity primary key,
    public_id text not null unique,
    participant_id bigint not null references competition.participants(id),
    cycle_id bigint not null references competition.forecast_cycles(id),
    attempt_number smallint not null check (attempt_number > 0),
    received_at timestamptz not null default now(),
    data_cutoff timestamptz not null,
    model_version text not null check (char_length(model_version) between 1 and 64),
    git_commit text,
    payload_hash text not null,
    status text not null check (status in ('accepted', 'rejected', 'superseded')),
    rejection_reason text,
    schema_version text not null default '1.0' check (schema_version = '1.0'),
    client_run_id text check (client_run_id is null or char_length(client_run_id) between 1 and 128),
    idempotency_key text,
    trained_at timestamptz,
    training_data_end timestamptz,
    request_id text,
    unique (participant_id, cycle_id, attempt_number),
    unique (participant_id, cycle_id, payload_hash),
    unique (participant_id, idempotency_key),
    unique (id, participant_id, cycle_id)
);

create index submissions_participant_cycle_idx
    on competition.submissions (participant_id, cycle_id, received_at desc);

create index submissions_cycle_idx
    on competition.submissions (cycle_id, received_at desc);

create table competition.predictions (
    submission_id bigint not null references competition.submissions(id) on delete cascade,
    station_id text not null references catalog.stations(station_id),
    target_at timestamptz not null,
    predicted_value double precision not null check (
        predicted_value >= 0
        and predicted_value <> 'NaN'::float8
        and predicted_value not in ('Infinity'::float8, '-Infinity'::float8)
    ),
    primary key (submission_id, station_id, target_at)
);

create index predictions_target_idx
    on competition.predictions (target_at, station_id);

create index predictions_station_idx
    on competition.predictions (station_id, target_at);

create table competition.cycle_entries (
    cycle_id bigint not null references competition.forecast_cycles(id) on delete cascade,
    participant_id bigint not null references competition.participants(id) on delete cascade,
    official_submission_id bigint not null,
    first_submitted_at timestamptz not null,
    latest_submitted_at timestamptz not null,
    primary key (cycle_id, participant_id),
    foreign key (official_submission_id, participant_id, cycle_id)
        references competition.submissions(id, participant_id, cycle_id)
);

create index cycle_entries_submission_idx
    on competition.cycle_entries (official_submission_id);

create index cycle_entries_participant_idx
    on competition.cycle_entries (participant_id, cycle_id);

create table competition.score_components (
    scenario_id bigint not null references sim.scenarios(id) on delete cascade,
    participant_id bigint not null references competition.participants(id) on delete cascade,
    cycle_id bigint not null references competition.forecast_cycles(id) on delete cascade,
    submission_id bigint,
    station_id text not null references catalog.stations(station_id),
    target_at timestamptz not null,
    horizon_steps smallint not null check (horizon_steps between 1 and 4),
    actual_value integer not null check (actual_value >= 0),
    predicted_value double precision,
    absolute_error double precision not null check (absolute_error >= 0),
    within_20 boolean not null,
    was_missing boolean not null default false,
    resolved_at timestamptz not null default now(),
    primary key (participant_id, cycle_id, station_id, target_at),
    foreign key (cycle_id, station_id, target_at)
        references competition.cycle_targets(cycle_id, station_id, target_at),
    foreign key (submission_id, participant_id, cycle_id)
        references competition.submissions(id, participant_id, cycle_id)
);

create index score_components_participant_time_idx
    on competition.score_components (scenario_id, participant_id, target_at);

create index score_components_resolution_idx
    on competition.score_components (scenario_id, target_at);

create table competition.score_snapshots (
    id bigint generated always as identity primary key,
    scenario_id bigint not null references sim.scenarios(id) on delete cascade,
    participant_id bigint not null references competition.participants(id) on delete cascade,
    calculated_at timestamptz not null default now(),
    window_type text not null check (window_type in ('cumulative', 'rolling_24h', 'current_cycle')),
    window_start timestamptz not null,
    window_end timestamptz not null,
    accuracy double precision not null check (accuracy between 0 and 100),
    raw_wape double precision not null check (raw_wape >= 0),
    accuracy_at_20 double precision not null check (accuracy_at_20 between 0 and 100),
    coverage double precision not null check (coverage between 0 and 1),
    rank integer check (rank > 0),
    check (window_start <= window_end)
);

create index score_snapshots_latest_idx
    on competition.score_snapshots (scenario_id, window_type, calculated_at desc, participant_id);

create index score_snapshots_participant_idx
    on competition.score_snapshots (participant_id, calculated_at desc);

create view competition.leaderboard_latest as
select distinct on (s.scenario_id, s.participant_id, s.window_type)
    s.scenario_id,
    s.participant_id,
    p.display_name,
    p.kind,
    p.eligible,
    s.window_type,
    s.accuracy,
    s.raw_wape,
    s.accuracy_at_20,
    s.coverage,
    s.rank,
    s.calculated_at
from competition.score_snapshots s
join competition.participants p on p.id = s.participant_id
order by s.scenario_id, s.participant_id, s.window_type, s.calculated_at desc;

create view competition.public_scenarios as
select id, code, state, history_start, competition_start, competition_end
from sim.scenarios
where state in ('scheduled', 'running', 'frozen', 'revealed');

create table ops.scheduler_heartbeats (
    instance_name text primary key,
    heartbeat_at timestamptz not null,
    status text not null,
    details jsonb not null default '{}'::jsonb
);

create table ops.job_runs (
    id bigint generated always as identity primary key,
    job_type text not null,
    scenario_id bigint references sim.scenarios(id),
    started_at timestamptz not null default now(),
    completed_at timestamptz,
    status text not null check (status in ('running', 'succeeded', 'failed', 'skipped')),
    details jsonb not null default '{}'::jsonb,
    error_message text
);

create index job_runs_scenario_time_idx on ops.job_runs (scenario_id, started_at desc);

create table ops.audit_events (
    id bigint generated always as identity primary key,
    occurred_at timestamptz not null default now(),
    actor_type text not null,
    actor_id text,
    action text not null,
    entity_type text not null,
    entity_id text,
    request_id text,
    metadata jsonb not null default '{}'::jsonb
);

create index audit_events_time_idx on ops.audit_events (occurred_at desc, id desc);
create index audit_events_entity_idx on ops.audit_events (entity_type, entity_id, occurred_at desc);

commit;
