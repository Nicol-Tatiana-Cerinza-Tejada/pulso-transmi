begin;

-- El endpoint de leaderboard devuelve snapshots acumulados y rolling_24h, pero
-- no los persistía en el esquema propio. Esta tabla conserva solo los campos
-- públicos necesarios para el dashboard; nunca guarda API keys, payloads ni
-- recibos de submissions.
create table if not exists leaderboard_snapshots (
    id bigint generated always as identity primary key,
    window_type text not null check (window_type in ('cumulative', 'rolling_24h')),
    display_name text not null,
    kind text,
    eligible boolean,
    accuracy numeric(7, 4),
    raw_wape numeric(12, 6),
    accuracy_at_20 numeric(7, 4),
    coverage numeric(7, 6),
    rank integer check (rank is null or rank > 0),
    calculated_at timestamptz not null default now()
);

comment on table leaderboard_snapshots is
    'Snapshots públicos del leaderboard acumulado y rolling 24h; no contiene credenciales, payloads ni recibos.';

create index if not exists leaderboard_snapshots_latest_idx
    on leaderboard_snapshots (window_type, display_name, calculated_at desc);

-- RLS queda habilitado para todas las tablas operativas. El rol service_role
-- conserva el acceso de los jobs; anon/authenticated no tienen SELECT directo.
alter table stations enable row level security;
alter table observations enable row level security;
alter table collector_runs enable row level security;
alter table model_versions enable row level security;
alter table predictions enable row level security;
alter table actuals enable row level security;
alter table metrics enable row level security;
alter table drift_signals enable row level security;
alter table submission_receipts enable row level security;
alter table model_promotion_events enable row level security;
alter table leaderboard_snapshots enable row level security;

-- Las vistas son SECURITY DEFINER y publican una proyección explícita. Estas
-- policies dejan documentado el acceso server-side de los jobs sin abrir las
-- tablas base al rol anon/publishable.
do $$
declare
    table_name text;
begin
    foreach table_name in array array[
        'stations', 'observations', 'collector_runs', 'model_versions',
        'predictions', 'actuals', 'metrics', 'drift_signals',
        'submission_receipts', 'model_promotion_events', 'leaderboard_snapshots'
    ] loop
        execute format('drop policy if exists %I on public.%I', table_name || '_service_role_select', table_name);
        execute format(
            'create policy %I on public.%I for select to service_role using (true)',
            table_name || '_service_role_select', table_name
        );
        execute format('revoke all on table public.%I from anon, authenticated', table_name);
    end loop;
end $$;

create or replace view public.v_accuracy_timeline as
with cycle_scores as (
    select
        cycle_id,
        max(calculated_at) as calculated_at,
        avg(accuracy)::numeric(7, 4) as accuracy,
        avg(wape)::numeric(12, 6) as wape,
        avg(coverage)::numeric(7, 6) as coverage,
        sum(predictions_count)::bigint as predictions_count,
        sum(actuals_count)::bigint as actuals_count
    from public.metrics
    group by cycle_id
)
select
    cycle_id,
    calculated_at,
    accuracy,
    wape,
    coverage,
    predictions_count,
    actuals_count,
    avg(accuracy) over (
        order by calculated_at
        range between interval '24 hours' preceding and current row
    )::numeric(7, 4) as accuracy_rolling_24h
from cycle_scores;

comment on view public.v_accuracy_timeline is
    'Accuracy oficial por ciclo: promedio no ponderado de las estaciones y rolling temporal de 24 horas.';

create or replace view public.v_accuracy_by_station as
with ranked as (
    select
        cycle_id,
        station_id,
        calculated_at,
        accuracy,
        wape,
        coverage,
        predictions_count,
        actuals_count,
        row_number() over (
            partition by station_id order by calculated_at desc, cycle_id desc
        ) as cycle_rank
    from public.metrics
)
select * from ranked where cycle_rank <= 12;

comment on view public.v_accuracy_by_station is
    'Accuracy por estación limitada a los últimos 12 ciclos disponibles.';

create or replace view public.v_champion_current as
select
    version,
    created_at as active_since,
    metric as validation_metric,
    git_commit,
    data_cutoff,
    artifact_path
from public.model_versions
where status = 'champion';

create or replace view public.v_model_history as
select
    event.id as event_id,
    event.action,
    event.status,
    event.reason,
    event.created_at,
    event.completed_at,
    event.requested_version as version,
    requested.metric as validation_metric,
    requested.git_commit,
    event.previous_version,
    previous.status as previous_status,
    event.verification
from public.model_promotion_events event
join public.model_versions requested on requested.version = event.requested_version
left join public.model_versions previous on previous.version = event.previous_version;

create or replace view public.v_drift_signals as
select
    id,
    station_id,
    detected_at,
    signal_type,
    case
        when lower(coalesce(details->>'severity', '')) in ('low', 'medium', 'high', 'critical')
            then lower(details->>'severity')
        when score >= 0.5 then 'high'
        when score >= 0.2 then 'medium'
        else 'low'
    end as severity,
    score,
    reference_value,
    current_value,
    window_start,
    window_end,
    status
from public.drift_signals
where status = 'open';

-- No se consultan submission_receipts ni sus payloads. Infer y evaluate se
-- representan mediante agregados de predicciones y métricas. Se conservan las
-- últimas 100 ejecuciones observables de cada pipeline.
create or replace view public.v_pipeline_runs as
with runs as (
    select
        'collector'::text as pipeline,
        id::text as run_id,
        started_at,
        finished_at,
        status,
        rows_new::bigint as rows_processed,
        error
    from public.collector_runs
    union all
    select
        'infer'::text as pipeline,
        cycle_id as run_id,
        min(created_at) as started_at,
        max(created_at) as finished_at,
        'succeeded'::text as status,
        count(*)::bigint as rows_processed,
        null::text as error
    from public.predictions
    group by cycle_id
    union all
    select
        'evaluate'::text as pipeline,
        cycle_id as run_id,
        min(calculated_at) as started_at,
        max(calculated_at) as finished_at,
        'succeeded'::text as status,
        count(*)::bigint as rows_processed,
        null::text as error
    from public.metrics
    group by cycle_id
), ranked as (
    select runs.*, row_number() over (
        partition by pipeline order by started_at desc, run_id desc
    ) as recent_rank
    from runs
)
select pipeline, run_id, started_at, finished_at, status, rows_processed, error
from ranked
where recent_rank <= 100;

comment on view public.v_pipeline_runs is
    'Ejecuciones recientes agregadas de collector, infer y evaluate; no expone submissions ni recibos.';

create or replace view public.v_leaderboard_snapshot as
with latest as (
    select
        snapshot.*,
        row_number() over (
            partition by window_type, display_name
            order by calculated_at desc, id desc
        ) as recency_rank
    from public.leaderboard_snapshots snapshot
)
select
    window_type,
    display_name,
    kind,
    eligible,
    accuracy,
    raw_wape,
    accuracy_at_20,
    coverage,
    rank as position,
    calculated_at
from latest
where recency_rank = 1;

comment on view public.v_leaderboard_snapshot is
    'Último snapshot público por participante para las ventanas cumulative y rolling_24h.';

-- En PostgreSQL las policies aplican a tablas, no a views. El equivalente
-- seguro para una vista de dashboard es otorgar SELECT solo sobre estas siete
-- proyecciones explícitas y mantener revocado el acceso a las tablas base.
grant select on public.v_accuracy_timeline to anon, authenticated;
grant select on public.v_accuracy_by_station to anon, authenticated;
grant select on public.v_champion_current to anon, authenticated;
grant select on public.v_model_history to anon, authenticated;
grant select on public.v_drift_signals to anon, authenticated;
grant select on public.v_pipeline_runs to anon, authenticated;
grant select on public.v_leaderboard_snapshot to anon, authenticated;

commit;
