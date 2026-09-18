from __future__ import annotations

import argparse
import asyncio
import json
import re
import sys
import uuid

import asyncpg

from app.security import generate_api_key
from app.portal import identity_hash
from app.settings import get_settings


def validate_avatar_records(records: object) -> list[tuple[str, int]]:
    if not isinstance(records, list) or not records:
        raise ValueError("stdin must contain a non-empty JSON array")
    validated: list[tuple[str, int]] = []
    participant_ids: set[str] = set()
    avatar_indexes: set[int] = set()
    for item in records:
        if not isinstance(item, dict):
            raise ValueError("Each avatar mapping must be an object")
        participant_id = str(item.get("participant_id", "")).strip()
        avatar_index = item.get("avatar_index")
        if not re.fullmatch(r"stu_[a-f0-9]{32}", participant_id):
            raise ValueError("Each avatar mapping needs a valid participant_id")
        if (
            isinstance(avatar_index, bool)
            or not isinstance(avatar_index, int)
            or not 0 <= avatar_index <= 35
        ):
            raise ValueError("avatar_index must be an integer between 0 and 35")
        if participant_id in participant_ids:
            raise ValueError("participant_id values must be unique")
        if avatar_index in avatar_indexes:
            raise ValueError("avatar_index values must be unique within the cohort")
        participant_ids.add(participant_id)
        avatar_indexes.add(avatar_index)
        validated.append((participant_id, avatar_index))
    return validated


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


async def import_roster(scenario_code: str, cohort_code: str) -> None:
    settings = get_settings()
    try:
        records = json.load(sys.stdin)
    except json.JSONDecodeError as exc:
        raise SystemExit("stdin must contain a JSON array") from exc
    if not isinstance(records, list) or not records:
        raise SystemExit("stdin must contain a non-empty JSON array")
    if settings.portal_identity_pepper == "development-only-change-me":
        raise SystemExit("PORTAL_IDENTITY_PEPPER must be configured before importing")

    async with asyncpg.create_pool(settings.database_url, min_size=1, max_size=1) as pool:
        async with pool.acquire() as connection, connection.transaction():
            scenario_id = await connection.fetchval(
                "select id from sim.scenarios where code=$1", scenario_code
            )
            if scenario_id is None:
                raise SystemExit(f"Scenario not found: {scenario_code}")
            for item in records:
                if not isinstance(item, dict):
                    raise SystemExit("Each roster item must be an object")
                name = str(item.get("name", "")).strip()
                email = str(item.get("email", "")).strip().lower()
                student_code = str(item.get("student_code", "")).strip()
                section = str(item.get("section", "")).strip().upper()
                raw_avatar_index = item.get("avatar_index")
                if raw_avatar_index is None:
                    avatar_index = None
                elif (
                    isinstance(raw_avatar_index, bool)
                    or not isinstance(raw_avatar_index, int)
                    or not 0 <= raw_avatar_index <= 35
                ):
                    raise SystemExit("avatar_index must be an integer between 0 and 35")
                else:
                    avatar_index = raw_avatar_index
                if not name or not email.endswith("@est.uexternado.edu.co"):
                    raise SystemExit("Each student needs a name and institutional email")
                if not student_code or section not in {"A", "B"}:
                    raise SystemExit("Each student needs a student_code and section A or B")

                email_digest = identity_hash(email, "email", settings.portal_identity_pepper)
                code_digest = identity_hash(
                    student_code, "student_code", settings.portal_identity_pepper
                )
                stable_suffix = email_digest.hex()[:16]
                participant_id = await connection.fetchval(
                    """
                    insert into competition.participants (
                        public_id, display_name, slug, cohort_code, section_code,
                        login_email_hash, login_student_code_hash, avatar_index
                    )
                    values ($1,$2,$3,$4,$5,$6,$7,$8)
                    on conflict (login_email_hash) where login_email_hash is not null
                    do update set
                        display_name=excluded.display_name,
                        cohort_code=excluded.cohort_code,
                        section_code=excluded.section_code,
                        login_student_code_hash=excluded.login_student_code_hash,
                        avatar_index=coalesce(
                            excluded.avatar_index,
                            competition.participants.avatar_index
                        )
                    returning id
                    """,
                    f"stu_{uuid.uuid4().hex}",
                    name,
                    f"student-{stable_suffix}",
                    cohort_code,
                    section,
                    email_digest,
                    code_digest,
                    avatar_index,
                )
                await connection.execute(
                    """
                    insert into competition.participant_scenarios (participant_id,scenario_id)
                    values ($1,$2)
                    on conflict (participant_id,scenario_id)
                    do update set status='active'
                    """,
                    participant_id,
                    scenario_id,
                )
    print(f"Imported {len(records)} active students into cohort {cohort_code}.")


async def assign_avatar_map(cohort_code: str) -> None:
    settings = get_settings()
    try:
        mappings = validate_avatar_records(json.load(sys.stdin))
    except (json.JSONDecodeError, ValueError) as exc:
        raise SystemExit(str(exc)) from exc

    requested_ids = [participant_id for participant_id, _ in mappings]
    requested_indexes = {avatar_index for _, avatar_index in mappings}
    async with asyncpg.create_pool(settings.database_url, min_size=1, max_size=1) as pool:
        async with pool.acquire() as connection, connection.transaction():
            cohort_rows = await connection.fetch(
                """
                select public_id, avatar_index
                from competition.participants
                where cohort_code=$1 and kind='student'
                for update
                """,
                cohort_code,
            )
            cohort_by_id = {row["public_id"]: row for row in cohort_rows}
            missing = sorted(set(requested_ids) - set(cohort_by_id))
            if missing:
                raise SystemExit(
                    f"Participants do not belong to cohort {cohort_code}: {', '.join(missing)}"
                )
            requested_id_set = set(requested_ids)
            occupied = {
                row["avatar_index"]
                for row in cohort_rows
                if row["public_id"] not in requested_id_set
                and row["avatar_index"] is not None
            }
            collisions = sorted(requested_indexes & occupied)
            if collisions:
                raise SystemExit(
                    f"Avatar indexes already assigned in cohort {cohort_code}: {collisions}"
                )
            await connection.execute(
                """
                update competition.participants
                set avatar_index=null
                where public_id=any($1::text[])
                """,
                requested_ids,
            )
            await connection.executemany(
                """
                update competition.participants
                set avatar_index=$2
                where public_id=$1 and cohort_code=$3 and kind='student'
                """,
                [
                    (participant_id, avatar_index, cohort_code)
                    for participant_id, avatar_index in mappings
                ],
            )
            await connection.executemany(
                """
                insert into ops.audit_events
                    (actor_type, actor_id, action, entity_type, entity_id, metadata)
                values ('admin','avatar-map-cli','participant.avatar_assigned',
                        'participant',$1,jsonb_build_object(
                            'cohort_code',$3::text,
                            'avatar_index',$2::integer
                        ))
                """,
                [
                    (participant_id, avatar_index, cohort_code)
                    for participant_id, avatar_index in mappings
                ],
            )
    print(f"Assigned {len(mappings)} unique avatars in cohort {cohort_code}.")


async def revoke_api_key(participant_public_id: str) -> None:
    settings = get_settings()
    async with asyncpg.create_pool(settings.database_url, min_size=1, max_size=1) as pool:
        async with pool.acquire() as connection, connection.transaction():
            participant_id = await connection.fetchval(
                "select id from competition.participants where public_id=$1",
                participant_public_id,
            )
            if participant_id is None:
                raise SystemExit("Participant not found")
            result = await connection.execute(
                """
                update competition.api_keys set revoked_at=now()
                where participant_id=$1 and revoked_at is null
                """,
                participant_id,
            )
            await connection.execute(
                """
                update competition.participants set credential_claimed_at=null
                where id=$1
                """,
                participant_id,
            )
    print(f"Revoked active keys for {participant_public_id}: {result}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Pulso TransMi participant administration")
    sub = parser.add_subparsers(dest="command", required=True)
    create = sub.add_parser("create-participant")
    create.add_argument("--name", required=True)
    create.add_argument("--slug", required=True)
    create.add_argument("--scenario", required=True)
    roster = sub.add_parser("import-roster")
    roster.add_argument("--scenario", required=True)
    roster.add_argument("--cohort", required=True)
    revoke = sub.add_parser("revoke-api-key")
    revoke.add_argument("--participant-id", required=True)
    avatars = sub.add_parser("assign-avatar-map")
    avatars.add_argument("--cohort", required=True)
    args = parser.parse_args()
    if args.command == "create-participant":
        if not re.fullmatch(r"[a-z0-9][a-z0-9-]{1,62}", args.slug):
            raise SystemExit("slug must use lowercase letters, numbers and hyphens")
        asyncio.run(create_participant(args.name, args.slug, args.scenario))
    elif args.command == "import-roster":
        asyncio.run(import_roster(args.scenario, args.cohort))
    elif args.command == "revoke-api-key":
        asyncio.run(revoke_api_key(args.participant_id))
    elif args.command == "assign-avatar-map":
        asyncio.run(assign_avatar_map(args.cohort))


if __name__ == "__main__":
    main()
