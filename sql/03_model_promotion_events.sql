begin;

create table if not exists model_promotion_events (
    id bigint generated always as identity primary key,
    requested_version text not null references model_versions(version),
    previous_version text references model_versions(version),
    action text not null default 'rollback' check (action in ('promotion', 'rollback')),
    reason text not null check (btrim(reason) <> ''),
    status text not null default 'pending' check (status in ('pending', 'succeeded', 'failed')),
    verification jsonb not null default '{}'::jsonb,
    created_at timestamptz not null default now(),
    completed_at timestamptz,
    error text
);

comment on table model_promotion_events is
    'Auditoría de rollbacks/promociones de modelos: versión solicitada, champion anterior, motivo, verificación y timestamps.';

create index if not exists model_promotion_events_created_idx
    on model_promotion_events (created_at desc);

create index if not exists model_promotion_events_version_idx
    on model_promotion_events (requested_version, created_at desc);

-- Permite actualizar de forma segura una instalación que creó inicialmente
-- esta tabla aceptando solo eventos de rollback.
alter table model_promotion_events
    drop constraint if exists model_promotion_events_action_check;

alter table model_promotion_events
    add constraint model_promotion_events_action_check
    check (action in ('promotion', 'rollback'));

commit;
