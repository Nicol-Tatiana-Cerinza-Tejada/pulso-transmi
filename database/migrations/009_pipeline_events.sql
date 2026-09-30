begin;

create table if not exists public.pipeline_events (
    id bigint generated always as identity primary key,
    pipeline text not null check (pipeline in ('infer', 'retrain', 'monitor', 'evaluate')),
    run_id text not null,
    cycle_id text,
    model_version text,
    started_at timestamptz not null default now(),
    finished_at timestamptz,
    status text not null check (status in ('running', 'succeeded', 'skipped', 'failed')),
    error text,
    request_id text,
    details jsonb not null default '{}'::jsonb,
    check (finished_at is null or finished_at >= started_at)
);

create index if not exists pipeline_events_recent_idx
    on public.pipeline_events (pipeline, started_at desc);

comment on table public.pipeline_events is
    'Auditoría de inferencia, retraining, monitor y evaluación, incluidos fallos y razones de decisión.';

alter table public.pipeline_events enable row level security;

drop policy if exists pipeline_events_service_role_select on public.pipeline_events;
create policy pipeline_events_service_role_select
    on public.pipeline_events for select to service_role using (true);

create or replace view public.v_drift_history as
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
order by detected_at desc
limit 100;

grant select on public.v_drift_history to anon, authenticated;
grant select on public.pipeline_events to service_role;

-- Solicita a PostgREST refrescar su caché de esquema después de la migración.
notify pgrst, 'reload schema';

commit;
