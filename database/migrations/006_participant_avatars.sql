begin;

alter table competition.participants
    add column if not exists avatar_index smallint;

alter table competition.participants
    drop constraint if exists participants_avatar_index_check;

alter table competition.participants
    add constraint participants_avatar_index_check
    check (avatar_index between 0 and 35) not valid;

alter table competition.participants
    validate constraint participants_avatar_index_check;

create unique index if not exists participants_cohort_avatar_key
    on competition.participants (cohort_code, avatar_index)
    where kind = 'student' and avatar_index is not null;

grant select on competition.score_snapshots to academy_api;

insert into ops.schema_migrations (version)
values ('006_participant_avatars')
on conflict (version) do nothing;

commit;
