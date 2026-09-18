begin;

revoke all on schema catalog, sim, competition, ops from public;
revoke all on all tables in schema catalog, sim, competition, ops from public;

grant usage on schema catalog, competition, ops to academy_api;
grant select on catalog.stations to academy_api;
grant select on competition.scenario_clock,
    competition.participants,
    competition.participant_scenarios,
    competition.observations,
    competition.public_time_context,
    competition.public_events,
    competition.forecast_cycles,
    competition.cycle_targets,
    competition.score_snapshots,
    competition.leaderboard_latest,
    competition.public_scenarios
to academy_api;
grant select, update on competition.api_keys to academy_api;
grant insert on competition.api_keys to academy_api;
grant update (credential_claimed_at) on competition.participants to academy_api;
grant select, insert, update on competition.portal_sessions to academy_api;
grant select, insert, update on competition.submissions to academy_api;
grant select, insert on competition.predictions to academy_api;
grant select, insert, update on competition.cycle_entries to academy_api;
grant usage, select on all sequences in schema competition to academy_api;
grant insert on ops.audit_events to academy_api;
grant usage, select on all sequences in schema ops to academy_api;

grant usage on schema catalog, sim, competition, ops to academy_scheduler;
grant select on all tables in schema catalog, sim, competition to academy_scheduler;
grant select, insert, update on all tables in schema competition to academy_scheduler;
grant select, insert, update on all tables in schema ops to academy_scheduler;
grant usage, select on all sequences in schema competition, ops to academy_scheduler;

commit;
