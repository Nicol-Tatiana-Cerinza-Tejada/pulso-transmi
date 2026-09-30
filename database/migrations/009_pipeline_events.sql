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

commit;
