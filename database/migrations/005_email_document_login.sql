begin;

alter table competition.portal_sessions
    add column if not exists preferred_name text;

alter table competition.portal_sessions
    drop constraint if exists portal_sessions_preferred_name_check;

alter table competition.portal_sessions
    add constraint portal_sessions_preferred_name_check
    check (
        preferred_name is null
        or char_length(btrim(preferred_name)) between 1 and 160
    );

-- El nombre deja de participar en la autenticación. Se eliminan sus firmas
-- históricas para que correo institucional + documento sean el único match.
update competition.participants
set login_name_hash = null
where login_name_hash is not null;

insert into ops.schema_migrations (version)
values ('005_email_document_login')
on conflict (version) do nothing;

commit;
