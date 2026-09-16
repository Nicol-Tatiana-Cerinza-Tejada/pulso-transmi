from __future__ import annotations

import argparse
import asyncio
import re
import uuid

import asyncpg

from app.security import generate_api_key
from app.settings import get_settings


async def create_participant(display_name: str, slug: str, scenario_code: str) -> None:
    settings = get_settings()
    raw_key, prefix, secret_hash = generate_api_key()
    async with asyncpg.create_pool(settings.database_url, min_size=1, max_size=1) as pool:
        async with pool.acquire() as connection, connection.transaction():
            scenario_id = await connection.fetchval("select id from sim.scenarios where code=$1", scenario_code)
            if scenario_id is None:
                raise SystemExit(f"Scenario not found: {scenario_code}")
            participant_id = await connection.fetchval(
                """
                insert into competition.participants (public_id,display_name,slug)
                values ($1,$2,$3) returning id
                """,
                f"stu_{uuid.uuid4().hex}", display_name, slug,
            )
            await connection.execute(
                "insert into competition.participant_scenarios (participant_id,scenario_id) values ($1,$2)",
                participant_id, scenario_id,
            )
            await connection.execute(
                "insert into competition.api_keys (participant_id,key_prefix,secret_hash) values ($1,$2,$3)",
                participant_id, prefix, secret_hash,
            )
    print("API key (shown once):")
    print(raw_key)


def main() -> None:
    parser = argparse.ArgumentParser(description="Pulso TransMi participant administration")
    sub = parser.add_subparsers(dest="command", required=True)
    create = sub.add_parser("create-participant")
    create.add_argument("--name", required=True)
    create.add_argument("--slug", required=True)
    create.add_argument("--scenario", required=True)
    args = parser.parse_args()
    if not re.fullmatch(r"[a-z0-9][a-z0-9-]{1,62}", args.slug):
        raise SystemExit("slug must use lowercase letters, numbers and hyphens")
    asyncio.run(create_participant(args.name, args.slug, args.scenario))


if __name__ == "__main__":
    main()
