\set ON_ERROR_STOP on

begin;

insert into catalog.stations (station_id, name, latitude, longitude, source_metadata)
values
    ('03000', 'Portal Suba', 4.74681506, -74.09427889, '{"source":"starter-v1"}'::jsonb),
    ('05000', 'Portal Américas', 4.62938130, -74.17305845, '{"source":"starter-v1"}'::jsonb),
    ('09000', 'Portal Usme', 4.53171458, -74.11939098, '{"source":"starter-v1"}'::jsonb),
    ('02300', 'Calle 100 - Marketmedios', 4.68394667, -74.05769591, '{"source":"starter-v1"}'::jsonb),
    ('09122', 'Calle 72', 4.65823884, -74.06206854, '{"source":"starter-v1"}'::jsonb),
    ('05100', 'Banderas', 4.63130064, -74.14576938, '{"source":"starter-v1"}'::jsonb),
    ('07111', 'Ricaurte - NQS', 4.61168620, -74.09386888, '{"source":"starter-v1"}'::jsonb),
    ('06000', 'Portal El Dorado – C.C. NUESTRO BOGOTÁ', 4.68160430, -74.12139545, '{"source":"starter-v1"}'::jsonb),
    ('07107', 'Universidad Nacional', 4.63711879, -74.07932113, '{"source":"starter-v1"}'::jsonb),
    ('06111', 'Universidades – CityU', 4.60464286, -74.06730954, '{"source":"starter-v1"}'::jsonb),
    ('07105', 'Movistar Arena', 4.65003852, -74.07834591, '{"source":"starter-v1"}'::jsonb),
    ('10009', 'Museo Nacional', 4.61524712, -74.06922646, '{"source":"starter-v1"}'::jsonb)
on conflict (station_id) do update
set name = excluded.name,
    latitude = excluded.latitude,
    longitude = excluded.longitude,
    source_metadata = catalog.stations.source_metadata || excluded.source_metadata;

insert into sim.generator_versions (version, git_commit, config_schema_version)
values ('practice-bootstrap-v1', '0000000', 1)
on conflict (version) do nothing;

insert into sim.scenarios (
    code, generator_version_id, state, history_start, competition_start,
    competition_end, seed_ciphertext, config, config_hash
)
select
    'practice-20260918', id, 'scheduled',
    '2026-07-26T00:00:00-05:00'::timestamptz,
    '2026-09-09T00:00:00-05:00'::timestamptz,
    '2026-09-10T00:00:00-05:00'::timestamptz,
    decode('00', 'hex'),
    '{"mode":"practice","scoring":false,"generates_data":false}'::jsonb,
    'practice-bootstrap-v1'
from sim.generator_versions
where version = 'practice-bootstrap-v1'
on conflict (code) do nothing;

insert into sim.scenario_stations (
    scenario_id, station_id, display_order, is_benchmark, archetype, parameters
)
select
    s.id,
    st.station_id,
    row_number() over (order by st.station_id)::smallint,
    true,
    'interchange',
    '{}'::jsonb
from sim.scenarios s
cross join catalog.stations st
where s.code = 'practice-20260918'
  and st.source_metadata->>'source' = 'starter-v1'
on conflict (scenario_id, station_id) do nothing;

insert into competition.forecast_cycles (
    public_id, scenario_id, origin_at, data_cutoff, opens_at, closes_at,
    target_start_at, target_end_at, state
)
select
    'cyc_practice_20260918', id,
    '2026-09-08T23:45:00-05:00'::timestamptz,
    '2026-09-08T23:45:00-05:00'::timestamptz,
    now(),
    date_trunc('day', now() at time zone 'America/Bogota') at time zone 'America/Bogota' + interval '23 hours 59 minutes',
    '2026-09-09T00:00:00-05:00'::timestamptz,
    '2026-09-09T00:00:00-05:00'::timestamptz,
    'open'
from sim.scenarios
where code = 'practice-20260918'
on conflict (public_id) do update
set opens_at = least(competition.forecast_cycles.opens_at, excluded.opens_at),
    closes_at = excluded.closes_at,
    state = 'open';

insert into competition.cycle_targets (cycle_id, station_id, target_at, horizon_steps)
select
    c.id,
    ss.station_id,
    '2026-09-09T00:00:00-05:00'::timestamptz,
    1
from competition.forecast_cycles c
join sim.scenario_stations ss on ss.scenario_id = c.scenario_id
where c.public_id = 'cyc_practice_20260918'
on conflict (cycle_id, station_id, target_at) do nothing;

commit;
