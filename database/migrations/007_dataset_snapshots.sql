begin;

create table if not exists public.dataset_snapshots (
    snapshot_id text primary key,
    created_at timestamptz not null default now(),
    sha256 text not null unique check (sha256 ~ '^[0-9a-f]{64}$'),
    artifact_path text not null,
    row_count integer not null check (row_count > 0),
    station_count integer not null check (station_count > 0),
    data_start timestamptz not null,
    data_end timestamptz not null check (data_end >= data_start),
    metadata jsonb not null default '{}'::jsonb
);

comment on table public.dataset_snapshots is
    'Snapshots inmutables del dataset de entrenamiento, identificados por SHA-256 y almacenados en Supabase Storage.';

alter table public.model_versions
    add column if not exists dataset_snapshot_id text references public.dataset_snapshots(snapshot_id);

create index if not exists model_versions_dataset_snapshot_idx
    on public.model_versions (dataset_snapshot_id);

commit;
