import asyncio
import logging

import asyncpg

from app.settings import get_settings


async def heartbeat(connection: asyncpg.Connection, instance_name: str) -> None:
    await connection.execute(
        """
        insert into ops.scheduler_heartbeats (instance_name, heartbeat_at, status)
        values ($1, now(), 'idle')
        on conflict (instance_name)
        do update set heartbeat_at = excluded.heartbeat_at, status = excluded.status
        """,
        instance_name,
    )


async def run() -> None:
    settings = get_settings()
    logging.basicConfig(level=settings.log_level)
    logger = logging.getLogger("pulso-transmi-scheduler")
    pool = await asyncpg.create_pool(
        settings.database_url,
        min_size=1,
        max_size=2,
        command_timeout=10,
    )
    logger.info("scheduler started in heartbeat-only mode")
    try:
        while True:
            try:
                async with pool.acquire() as connection:
                    await heartbeat(connection, settings.scheduler_instance)
            except Exception:
                logger.exception("scheduler heartbeat failed")
            await asyncio.sleep(settings.scheduler_poll_seconds)
    finally:
        await pool.close()


if __name__ == "__main__":
    asyncio.run(run())
