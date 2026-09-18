begin;

alter table competition.participants
    add column if not exists cohort_code text,
    add column if not exists section_code text,
    add column if not exists login_name_hash bytea,
    add column if not exists login_email_hash bytea,
    add column if not exists login_student_code_hash bytea,
    add column if not exists credential_claimed_at timestamptz;

create unique index if not exists participants_login_email_hash_key
    on competition.participants (login_email_hash)
    where login_email_hash is not null;

create unique index if not exists participants_login_student_code_hash_key
    on competition.participants (login_student_code_hash)
    where login_student_code_hash is not null;

create table if not exists competition.portal_sessions (
    token_hash text primary key,
    participant_id bigint not null references competition.participants(id) on delete cascade,
    created_at timestamptz not null default now(),
    expires_at timestamptz not null,
    last_seen_at timestamptz not null default now(),
    revoked_at timestamptz,
    check (created_at < expires_at)
);

create index if not exists portal_sessions_active_participant_idx
    on competition.portal_sessions (participant_id, expires_at desc)
    where revoked_at is null;

grant select, insert, update on competition.portal_sessions to academy_api;
grant select, insert, update on competition.portal_sessions to academy_scheduler;
grant insert on competition.api_keys to academy_api;
grant update (credential_claimed_at) on competition.participants to academy_api;

insert into ops.schema_migrations (version)
values ('004_student_portal')
on conflict (version) do nothing;

commit;
