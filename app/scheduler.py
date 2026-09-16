from __future__ import annotations

import asyncio
import json
import logging
from datetime import timedelta

import asyncpg

from app.settings import get_settings


async def heartbeat(
    connection: asyncpg.Connection,
    instance_name: str,
    status: str = "idle",
    details: dict[str, object] | None = None,
) -> None:
    await connection.execute(
        """
        insert into ops.scheduler_heartbeats (instance_name, heartbeat_at, status, details)
        values ($1, now(), $2, $3::jsonb)
        on conflict (instance_name) do update
        set heartbeat_at=excluded.heartbeat_at,
            status=excluded.status,
            details=excluded.details
        """,
        instance_name,
        status,
        json.dumps(details or {}),
    )


async def release_observations(
    connection: asyncpg.Connection, scenario_id: int, virtual_now
) -> int:
    result = await connection.execute(
        """
        insert into competition.observations
            (scenario_id, station_id, observed_at, value, released_at)
        select scenario_id, station_id, observed_at, actual_value, now()
        from sim.generated_truth
        where scenario_id=$1 and observed_at <= $2
        on conflict (scenario_id,station_id,observed_at) do nothing
        """,
        scenario_id,
        virtual_now,
    )
    await connection.execute(
        """
        insert into competition.public_time_context
            (scenario_id, observed_at, rain_observed, rain_forecast,
             temperature_observed, temperature_forecast, is_holiday, released_at)
        select scenario_id, observed_at, rain_actual, rain_forecast,
               temperature_actual, temperature_forecast, is_holiday, now()
        from sim.time_context
        where scenario_id=$1 and observed_at <= $2
        on conflict (scenario_id,observed_at) do nothing
        """,
        scenario_id,
        virtual_now,
    )
    await connection.execute(
        """
        insert into competition.public_events
            (scenario_id,event_code,event_type,latitude,longitude,
             starts_at,ends_at,size_band,released_at)
        select scenario_id, 'evt_' || id::text, event_type, latitude, longitude,
               starts_at, ends_at,
               case when intensity < 0.75 then 'small'
                    when intensity < 1.5 then 'medium' else 'large' end,
               now()
        from sim.events
        where scenario_id=$1 and announced_at <= $2
        on conflict (scenario_id,event_code) do nothing
        """,
        scenario_id,
        virtual_now,
    )
    return int(result.rsplit(" ", 1)[-1])


async def close_expired_cycles(connection: asyncpg.Connection, scenario_id: int) -> None:
    await connection.execute(
        """
        update competition.forecast_cycles
        set state='closed'
        where scenario_id=$1 and state='open' and closes_at <= now()
        """,
        scenario_id,
    )


async def open_cycle(
    connection: asyncpg.Connection,
    scenario_id: int,
    scenario_code: str,
    virtual_now,
    submission_window_minutes: int,
) -> str | None:
    if virtual_now.minute != 0:
        return None
    public_id = f"cyc_{scenario_code}_{virtual_now.strftime('%Y%m%dT%H%M%SZ')}"
    cycle = await connection.fetchrow(
        """
        insert into competition.forecast_cycles
            (public_id,scenario_id,origin_at,data_cutoff,opens_at,closes_at,
             target_start_at,target_end_at,state)
        values ($1,$2,$3::timestamptz,$3::timestamptz,now(),now()+$4::interval,
                $3::timestamptz+interval '15 minutes',
                $3::timestamptz+interval '60 minutes','open')
        on conflict (scenario_id,origin_at) do nothing
        returning id
        """,
        public_id,
        scenario_id,
        virtual_now,
        timedelta(minutes=submission_window_minutes),
    )
    if cycle is None:
        return None
    await connection.execute(
        """
        insert into competition.cycle_targets
            (cycle_id,station_id,target_at,horizon_steps)
        select $1::bigint, station_id,
               $2::timestamptz + make_interval(mins => horizon * 15), horizon
        from sim.scenario_stations
        cross join generate_series(1,4) as horizon
        where scenario_id=$3::bigint and is_benchmark
        """,
        cycle["id"],
        virtual_now,
        scenario_id,
    )
    return public_id


async def score_revealed_targets(
    connection: asyncpg.Connection,
    scenario_id: int,
    previous_virtual_now,
    virtual_now,
) -> int:
    result = await connection.execute(
        """
        insert into competition.score_components
            (scenario_id,participant_id,cycle_id,submission_id,station_id,
             target_at,horizon_steps,actual_value,predicted_value,
             absolute_error,within_20,was_missing,resolved_at)
        select c.scenario_id, ps.participant_id, c.id,
               e.official_submission_id, t.station_id, t.target_at,
               t.horizon_steps, truth.actual_value, p.predicted_value,
               abs(coalesce(p.predicted_value,0)-truth.actual_value),
               abs(coalesce(p.predicted_value,0)-truth.actual_value)
                   <= greatest(1,truth.actual_value)*0.20,
               p.predicted_value is null, now()
        from competition.forecast_cycles c
        join competition.cycle_targets t on t.cycle_id=c.id
        join sim.generated_truth truth
          on truth.scenario_id=c.scenario_id
         and truth.station_id=t.station_id
         and truth.observed_at=t.target_at
        join competition.participant_scenarios ps
          on ps.scenario_id=c.scenario_id
         and ps.status in ('active','finished')
         and ps.joined_at <= c.opens_at
        left join competition.cycle_entries e
          on e.cycle_id=c.id and e.participant_id=ps.participant_id
        left join competition.predictions p
          on p.submission_id=e.official_submission_id
         and p.station_id=t.station_id and p.target_at=t.target_at
        where c.scenario_id=$1
          and c.state in ('closed','resolved')
          and t.target_at > $2 and t.target_at <= $3
        on conflict (participant_id,cycle_id,station_id,target_at) do nothing
        """,
        scenario_id,
        previous_virtual_now,
        virtual_now,
    )
    await connection.execute(
        """
        update competition.forecast_cycles c set state='resolved'
        where c.scenario_id=$1 and c.state='closed'
          and c.target_end_at <= $2
          and not exists (
            select 1 from competition.cycle_targets t
            where t.cycle_id=c.id and not exists (
              select 1 from competition.score_components s
              where s.cycle_id=t.cycle_id
                and s.station_id=t.station_id
                and s.target_at=t.target_at
            )
          )
        """,
        scenario_id,
        virtual_now,
    )
    return int(result.rsplit(" ", 1)[-1])


async def create_snapshot(
    connection: asyncpg.Connection,
    scenario_id: int,
    virtual_now,
    window_type: str,
    window_start,
) -> None:
    await connection.execute(
        """
        with selected as (
          select * from competition.score_components
          where scenario_id=$1 and target_at > $2 and target_at <= $3
        ), station_scores as (
          select participant_id, station_id,
                 sum(absolute_error)/greatest(1,sum(actual_value)) as station_wape
          from selected group by participant_id,station_id
        ), official_accuracy as (
          select participant_id,
                 avg(greatest(0,1-station_wape))*100 as accuracy
          from station_scores group by participant_id
        ), diagnostics as (
          select participant_id,
                 sum(absolute_error)/greatest(1,sum(actual_value)) as raw_wape,
                 avg(within_20::int)*100 as accuracy_at_20,
                 avg((not was_missing)::int) as coverage
          from selected group by participant_id
        ), participant_scores as (
          select a.participant_id,a.accuracy,d.raw_wape,
                 d.accuracy_at_20,d.coverage
          from official_accuracy a join diagnostics d using (participant_id)
        ), ranked as (
          select *, dense_rank() over (order by accuracy desc,coverage desc) as position
          from participant_scores
        )
        insert into competition.score_snapshots
            (scenario_id,participant_id,calculated_at,window_type,
             window_start,window_end,accuracy,raw_wape,accuracy_at_20,coverage,rank)
        select $1,participant_id,now(),$4,$2,$3,
               accuracy,raw_wape,accuracy_at_20,coverage,position
        from ranked
        """,
        scenario_id,
        window_start,
        virtual_now,
        window_type,
    )


async def tick_scenario(
    connection: asyncpg.Connection,
    scenario: asyncpg.Record,
    release_interval_minutes: int,
    submission_window_minutes: int,
) -> dict[str, object] | None:
    locked = await connection.fetchval(
        "select pg_try_advisory_xact_lock(71001,$1::integer)", scenario["scenario_id"]
    )
    if not locked:
        return None
    clock = await connection.fetchrow(
        "select *, now() as database_now from competition.scenario_clock where scenario_id=$1 for update",
        scenario["scenario_id"],
    )
    if clock is None or clock["state"] != "running":
        return None
    if clock["last_tick_at"] is not None and clock["database_now"] < clock["last_tick_at"] + timedelta(minutes=release_interval_minutes):
        return None

    previous_virtual_now = clock["virtual_now"]
    virtual_now = min(
        previous_virtual_now + timedelta(minutes=release_interval_minutes),
        scenario["competition_end"],
    )
    await close_expired_cycles(connection, scenario["scenario_id"])
    released = await release_observations(connection, scenario["scenario_id"], virtual_now)
    scored = await score_revealed_targets(
        connection, scenario["scenario_id"], previous_virtual_now, virtual_now
    )
    cycle_id = await open_cycle(
        connection,
        scenario["scenario_id"],
        scenario["code"],
        virtual_now,
        submission_window_minutes,
    )
    await create_snapshot(
        connection,
        scenario["scenario_id"],
        virtual_now,
        "cumulative",
        scenario["competition_start"],
    )
    await create_snapshot(
        connection,
        scenario["scenario_id"],
        virtual_now,
        "rolling_24h",
        max(scenario["competition_start"], virtual_now - timedelta(hours=24)),
    )
    state = "completed" if virtual_now >= scenario["competition_end"] else "running"
    await connection.execute(
        """
        update competition.scenario_clock
        set virtual_now=$2,tick_number=tick_number+1,last_tick_at=now(),
            state=$3,version=version+1
        where scenario_id=$1
        """,
        scenario["scenario_id"],
        virtual_now,
        state,
    )
    return {
        "scenario": scenario["code"],
        "virtual_now": virtual_now.isoformat(),
        "released_observations": released,
        "score_components": scored,
        "opened_cycle": cycle_id,
        "state": state,
    }


async def scheduler_pass(pool: asyncpg.Pool, settings) -> list[dict[str, object]]:
    results: list[dict[str, object]] = []
    async with pool.acquire() as connection:
        scenarios = await connection.fetch(
            """
            select c.scenario_id,s.code,s.competition_start,s.competition_end
            from competition.scenario_clock c
            join sim.scenarios s on s.id=c.scenario_id
            where c.state='running' and s.state='running'
            order by c.scenario_id
            """
        )
    for scenario in scenarios:
        try:
            async with pool.acquire() as connection:
                async with connection.transaction():
                    result = await tick_scenario(
                        connection,
                        scenario,
                        settings.release_interval_minutes,
                        settings.submission_window_minutes,
                    )
            if result:
                async with pool.acquire() as connection:
                    await connection.execute(
                        """
                        insert into ops.job_runs
                            (job_type,scenario_id,started_at,completed_at,status,details)
                        values ('competition_tick',$1,now(),now(),'succeeded',$2::jsonb)
                        """,
                        scenario["scenario_id"],
                        json.dumps(result),
                    )
                results.append(result)
        except Exception as exc:
            async with pool.acquire() as connection:
                await connection.execute(
                    """
                    insert into ops.job_runs
                        (job_type,scenario_id,started_at,completed_at,status,error_message)
                    values ('competition_tick',$1,now(),now(),'failed',$2)
                    """,
                    scenario["scenario_id"],
                    str(exc)[:2000],
                )
            raise
    return results


async def run() -> None:
    settings = get_settings()
    logging.basicConfig(level=settings.log_level)
    logger = logging.getLogger("pulso-transmi-scheduler")
    pool = await asyncpg.create_pool(
        settings.database_url, min_size=1, max_size=2, command_timeout=30
    )
    logger.info("competition scheduler started")
    try:
        while True:
            try:
                async with pool.acquire() as connection:
                    await heartbeat(connection, settings.scheduler_instance, "running")
                results = await scheduler_pass(pool, settings)
                async with pool.acquire() as connection:
                    await heartbeat(
                        connection,
                        settings.scheduler_instance,
                        "idle",
                        {"ticks": results},
                    )
                for result in results:
                    logger.info("competition tick: %s", result)
            except Exception as exc:
                logger.exception("scheduler pass failed")
                async with pool.acquire() as connection:
                    await heartbeat(
                        connection,
                        settings.scheduler_instance,
                        "error",
                        {"error": str(exc)[:500]},
                    )
            await asyncio.sleep(settings.scheduler_poll_seconds)
    finally:
        await pool.close()


if __name__ == "__main__":
    asyncio.run(run())
