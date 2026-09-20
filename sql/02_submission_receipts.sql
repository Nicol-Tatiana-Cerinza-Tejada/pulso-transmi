begin;

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
