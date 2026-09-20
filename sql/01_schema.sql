begin;

-- Esquema local para ingestión, entrenamiento y evaluación de Pulso TransMi.
-- El API expone station_id como texto de cinco dígitos y todas las fechas como
-- timestamps con zona horaria; por eso no se usan enteros para esos identificadores.

create table if not exists stations (
    station_id text primary key,
    station_name text not null check (btrim(station_name) <> ''),
    corridor text,
    latitude double precision check (latitude between 4.0 and 5.0),
    longitude double precision check (longitude between -75.0 and -73.0),
    active boolean not null default true,
    source_metadata jsonb not null default '{}'::jsonb,
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now(),
    constraint stations_station_id_format check (station_id ~ '^[0-9]{5}$')
);

comment on table stations is
    'Catálogo de estaciones del API Pulso TransMi; station_id conserva los cinco dígitos oficiales.';

create table if not exists observations (
    station_id text not null references stations(station_id),
    ts timestamptz not null,
    value integer not null check (value between 0 and 100000),
    released_at timestamptz,
    ingested_at timestamptz not null default now(),
    source text not null default 'pulso-transmi-api',
    primary key (station_id, ts)
);

comment on table observations is
    'Histórico de demanda; corresponde a observed_at/demand del API y permite upsert por estación y timestamp.';

-- El PK ya proporciona este orden, pero el índice explícito documenta el patrón
-- de lectura de features y hace estable el nombre usado por los jobs.
create index if not exists observations_station_ts_idx
    on observations (station_id, ts);

create table if not exists collector_runs (
    id bigint generated always as identity primary key,
    started_at timestamptz not null default now(),
    finished_at timestamptz,
    cursor text,
    rows_new integer not null default 0 check (rows_new >= 0),
    status text not null default 'running' check (
        status in ('running', 'succeeded', 'partial', 'failed')
    ),
    error text,
    check (finished_at is null or finished_at >= started_at),
    check (status = 'failed' or error is null)
);

comment on table collector_runs is
    'Ejecuciones del colector del stream incremental, incluyendo el cursor opaco y filas nuevas procesadas.';

create index if not exists collector_runs_started_idx
    on collector_runs (started_at desc);

create table if not exists model_versions (
    version text primary key,
    created_at timestamptz not null default now(),
    data_cutoff timestamptz not null,
    git_commit text,
    features jsonb not null default '[]'::jsonb,
    metric numeric(12, 6),
    artifact_path text,
    status text not null default 'candidate' check (
        status in ('candidate', 'champion', 'retired')
    ),
    constraint model_versions_version_not_blank check (btrim(version) <> ''),
    constraint model_versions_metric_nonnegative check (metric is null or metric >= 0),
    constraint model_versions_git_commit_format check (
        git_commit is null or git_commit ~ '^[0-9a-fA-F]{7,40}$'
    )
);

comment on table model_versions is
    'Versiones de modelos entrenados, trazables al corte de datos, commit, features y artefacto desplegable.';

create unique index if not exists one_champion_model_version_idx
    on model_versions (status)
    where status = 'champion';

create table if not exists predictions (
    cycle_id text not null,
    station_id text not null references stations(station_id),
    target_at timestamptz not null,
    value numeric(12, 4) not null check (value between 0 and 100000),
    model_version text not null references model_versions(version),
    submission_id text,
    created_at timestamptz not null default now(),
    primary key (cycle_id, station_id, target_at),
    constraint predictions_cycle_id_format check (cycle_id ~ '^cyc_[A-Za-z0-9_-]{1,80}$'),
    constraint predictions_submission_id_format check (
        submission_id is null or submission_id ~ '^sub_[A-Za-z0-9_-]+$'
    )
);

comment on table predictions is
    'Predicciones enviadas al API, una por ciclo, estación y target; la clave compuesta permite upsert.';

create index if not exists predictions_station_target_idx
    on predictions (station_id, target_at);

create table if not exists actuals (
    cycle_id text not null,
    station_id text not null references stations(station_id),
    target_at timestamptz not null,
    value integer not null check (value between 0 and 100000),
    observed_at timestamptz,
    loaded_at timestamptz not null default now(),
    primary key (cycle_id, station_id, target_at),
    constraint actuals_cycle_id_format check (cycle_id ~ '^cyc_[A-Za-z0-9_-]{1,80}$')
);

comment on table actuals is
    'Valores reales revelados para evaluar targets de ciclos; provienen de observations cuando el target se libera.';

create index if not exists actuals_station_target_idx
    on actuals (station_id, target_at);

create table if not exists metrics (
    cycle_id text not null,
    station_id text not null references stations(station_id),
    calculated_at timestamptz not null default now(),
    accuracy numeric(7, 4) not null check (accuracy between 0 and 100),
    wape numeric(12, 6) check (wape is null or wape >= 0),
    coverage numeric(7, 6) check (coverage is null or coverage between 0 and 1),
    predictions_count integer not null default 0 check (predictions_count >= 0),
    actuals_count integer not null default 0 check (actuals_count >= 0),
    primary key (cycle_id, station_id),
    constraint metrics_cycle_id_format check (cycle_id ~ '^cyc_[A-Za-z0-9_-]{1,80}$')
);

comment on table metrics is
    'Métricas por estación y ciclo; accuracy sigue la escala 0..100 publicada por el leaderboard del API.';

create index if not exists metrics_station_calculated_idx
    on metrics (station_id, calculated_at desc);

create table if not exists drift_signals (
    id bigint generated always as identity primary key,
    station_id text references stations(station_id),
    detected_at timestamptz not null default now(),
    signal_type text not null,
    score numeric(14, 6),
    reference_value numeric(14, 6),
    current_value numeric(14, 6),
    window_start timestamptz,
    window_end timestamptz,
    status text not null default 'open' check (
        status in ('open', 'acknowledged', 'resolved')
    ),
    details jsonb not null default '{}'::jsonb,
    constraint drift_signals_window_order check (
        window_end is null or window_start is null or window_start <= window_end
    )
);

comment on table drift_signals is
    'Señales de cambio de distribución por estación o globales, con score, ventanas y estado operativo.';

create index if not exists drift_signals_station_detected_idx
    on drift_signals (station_id, detected_at desc);

create table if not exists submission_receipts (
    participant_id text not null,
    cycle_id text not null,
    attempt smallint not null check (attempt between 1 and 3),
    idempotency_key text not null unique,
    payload jsonb not null,
    submission_id text,
    receipt jsonb,
    status text not null default 'pending' check (
        status in ('pending', 'accepted', 'failed')
    ),
    error text,
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now(),
    primary key (participant_id, cycle_id, attempt),
    constraint submission_receipts_cycle_id_format check (
        cycle_id ~ '^cyc_[A-Za-z0-9_-]{1,80}$'
    )
);

comment on table submission_receipts is
    'Payloads, llaves idempotentes y recibos de submissions para reintentos seguros y máximo tres intentos por ciclo.';

create index if not exists submission_receipts_cycle_idx
    on submission_receipts (participant_id, cycle_id, attempt desc);

commit;
