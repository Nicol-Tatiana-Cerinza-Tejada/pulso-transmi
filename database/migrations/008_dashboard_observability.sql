begin;

alter table public.predictions
    add column if not exists horizon_minutes smallint;

alter table public.predictions
    drop constraint if exists predictions_horizon_minutes_check;

alter table public.predictions
    add constraint predictions_horizon_minutes_check
    check (horizon_minutes is null or horizon_minutes in (15, 30, 45, 60));

alter table public.model_versions
    add column if not exists training_metadata jsonb not null default '{}'::jsonb;

commit;
