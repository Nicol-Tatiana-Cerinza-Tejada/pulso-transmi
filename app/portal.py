from __future__ import annotations

import hashlib
import hmac
import secrets
import unicodedata
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

import asyncpg
from fastapi import HTTPException

from app.security import generate_api_key


@dataclass(frozen=True)
class PortalIdentity:
    participant_id: int
    public_id: str
    display_name: str
    cohort_code: str | None
    section_code: str | None
    token_hash: str


def _plain(value: str) -> str:
    normalized = unicodedata.normalize("NFKD", value.strip().lower())
    return "".join(char for char in normalized if not unicodedata.combining(char))


def normalize_identity(value: str, field: str) -> str:
    if field == "name":
        return " ".join(_plain(value).split())
    if field == "email":
        return value.strip().lower()
    if field == "student_code":
        return "".join(char for char in _plain(value) if char.isalnum())
    raise ValueError(f"Unsupported identity field: {field}")


def identity_hash(value: str, field: str, pepper: str) -> bytes:
    normalized = normalize_identity(value, field)
    return hmac.new(
        pepper.encode(), f"{field}:{normalized}".encode(), hashlib.sha256
    ).digest()


def session_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


async def login(
    pool: asyncpg.Pool,
    *,
    name: str,
    email: str,
    student_code: str,
    pepper: str,
    session_hours: int,
) -> tuple[PortalIdentity, str, datetime]:
    name_digest = identity_hash(name, "name", pepper)
    email_digest = identity_hash(email, "email", pepper)
    code_digest = identity_hash(student_code, "student_code", pepper)
    raw_token = secrets.token_urlsafe(32)
    token_digest = session_hash(raw_token)
    expires_at = datetime.now(timezone.utc) + timedelta(hours=session_hours)

    async with pool.acquire() as connection, connection.transaction():
        row = await connection.fetchrow(
            """
            select id, public_id, display_name, cohort_code, section_code
            from competition.participants
            where kind='student'
              and login_name_hash=$1
              and login_email_hash=$2
              and login_student_code_hash=$3
            """,
            name_digest,
            email_digest,
            code_digest,
        )
        if row is None:
            raise HTTPException(
                status_code=401,
                detail={
                    "code": "invalid_identity",
                    "message": "Los datos no coinciden con la matrícula activa.",
                },
            )
        await connection.execute(
            """
            insert into competition.portal_sessions
                (token_hash, participant_id, expires_at)
            values ($1,$2,$3)
            """,
            token_digest,
            row["id"],
            expires_at,
        )
    return (
        PortalIdentity(
            participant_id=row["id"],
            public_id=row["public_id"],
            display_name=row["display_name"],
            cohort_code=row["cohort_code"],
            section_code=row["section_code"],
            token_hash=token_digest,
        ),
        raw_token,
        expires_at,
    )


async def authenticate_session(
    pool: asyncpg.Pool, raw_token: str | None
) -> PortalIdentity:
    if not raw_token:
        raise HTTPException(
            status_code=401,
            detail={"code": "portal_session_required", "message": "Inicia sesión."},
        )
    token_digest = session_hash(raw_token)
    async with pool.acquire() as connection:
        row = await connection.fetchrow(
            """
            select p.id, p.public_id, p.display_name, p.cohort_code, p.section_code
            from competition.portal_sessions s
            join competition.participants p on p.id=s.participant_id
            where s.token_hash=$1 and s.revoked_at is null and s.expires_at > now()
            """,
            token_digest,
        )
        if row is None:
            raise HTTPException(
                status_code=401,
                detail={
                    "code": "portal_session_expired",
                    "message": "La sesión venció. Ingresa de nuevo.",
                },
            )
        await connection.execute(
            "update competition.portal_sessions set last_seen_at=now() where token_hash=$1",
            token_digest,
        )
    return PortalIdentity(
        participant_id=row["id"],
        public_id=row["public_id"],
        display_name=row["display_name"],
        cohort_code=row["cohort_code"],
        section_code=row["section_code"],
        token_hash=token_digest,
    )


async def logout(pool: asyncpg.Pool, identity: PortalIdentity) -> None:
    async with pool.acquire() as connection:
        await connection.execute(
            "update competition.portal_sessions set revoked_at=now() where token_hash=$1",
            identity.token_hash,
        )


async def issue_api_key(
    pool: asyncpg.Pool, identity: PortalIdentity
) -> dict[str, object]:
    raw_key, prefix, secret_hash = generate_api_key()
    async with pool.acquire() as connection, connection.transaction():
        await connection.execute(
            "select pg_advisory_xact_lock(9042026, $1::integer)",
            identity.participant_id,
        )
        active = await connection.fetchrow(
            """
            select key_prefix, created_at, last_used_at
            from competition.api_keys
            where participant_id=$1 and revoked_at is null
            order by created_at desc limit 1
            """,
            identity.participant_id,
        )
        if active is not None:
            raise HTTPException(
                status_code=409,
                detail={
                    "code": "api_key_already_issued",
                    "message": "La API key ya fue generada. Si la perdiste, solicita una rotación al profesor.",
                    "key_prefix": active["key_prefix"],
                },
            )
        created_at = await connection.fetchval(
            """
            insert into competition.api_keys (participant_id,key_prefix,secret_hash)
            values ($1,$2,$3) returning created_at
            """,
            identity.participant_id,
            prefix,
            secret_hash,
        )
        await connection.execute(
            "update competition.participants set credential_claimed_at=now() where id=$1",
            identity.participant_id,
        )
        await connection.execute(
            """
            insert into ops.audit_events
                (actor_type, actor_id, action, entity_type, entity_id, metadata)
            values ('participant',$1,'api_key.issued','api_key',$2,'{}'::jsonb)
            """,
            identity.public_id,
            prefix,
        )
    return {
        "api_key": raw_key,
        "key_prefix": prefix,
        "created_at": created_at,
        "shown_once": True,
    }


async def dashboard(pool: asyncpg.Pool, identity: PortalIdentity) -> dict[str, object]:
    async with pool.acquire() as connection:
        key = await connection.fetchrow(
            """
            select key_prefix, created_at, last_used_at
            from competition.api_keys
            where participant_id=$1 and revoked_at is null
            order by created_at desc limit 1
            """,
            identity.participant_id,
        )
        cycle = await connection.fetchrow(
            """
            select id, public_id, data_cutoff, opens_at, closes_at,
                   target_start_at, target_end_at, state,
                   (select count(*) from competition.cycle_targets t where t.cycle_id=c.id) as expected_predictions
            from competition.forecast_cycles c
            where state='open' and now() < closes_at
            order by opens_at desc limit 1
            """
        )
        submissions = await connection.fetch(
            """
            select s.public_id as submission_id, c.public_id as cycle_id, s.status,
                   s.attempt_number, s.received_at, s.model_version,
                   (select count(*) from competition.predictions p where p.submission_id=s.id) as prediction_count,
                   (e.official_submission_id=s.id) as is_official
            from competition.submissions s
            join competition.forecast_cycles c on c.id=s.cycle_id
            left join competition.cycle_entries e
              on e.cycle_id=s.cycle_id and e.participant_id=s.participant_id
            where s.participant_id=$1
            order by s.received_at desc limit 10
            """,
            identity.participant_id,
        )
    return {
        "participant": {
            "participant_id": identity.public_id,
            "display_name": identity.display_name,
            "cohort": identity.cohort_code,
            "section": identity.section_code,
        },
        "api_key": dict(key) if key else None,
        "cycle": dict(cycle) if cycle else None,
        "submissions": [dict(row) for row in submissions],
    }


async def cohort_board(
    pool: asyncpg.Pool, identity: PortalIdentity
) -> dict[str, object]:
    async with pool.acquire() as connection:
        cycle = await connection.fetchrow(
            """
            select id, public_id, scenario_id, state, opens_at, closes_at,
                   (select count(*) from competition.cycle_targets t where t.cycle_id=c.id) as expected_predictions
            from competition.forecast_cycles c
            order by (state='open' and now() < closes_at) desc, opens_at desc
            limit 1
            """
        )
        if cycle is None:
            return {"mode": "integration", "cycle": None, "data": [], "count": 0}
        rows = await connection.fetch(
            """
            select p.public_id as participant_id, p.display_name, p.section_code,
                   exists(
                     select 1 from competition.api_keys k
                     where k.participant_id=p.id and k.revoked_at is null
                   ) as api_key_active,
                   s.status as submission_status,
                   s.attempt_number,
                   s.received_at as last_submission_at,
                   s.model_version,
                   coalesce((
                     select count(*) from competition.predictions pr
                     where pr.submission_id=s.id
                   ), 0) as prediction_count,
                   lb.rank, lb.accuracy, lb.coverage, lb.calculated_at
            from competition.participant_scenarios ps
            join competition.participants p on p.id=ps.participant_id
            left join competition.cycle_entries ce
              on ce.participant_id=p.id and ce.cycle_id=$1
            left join competition.submissions s on s.id=ce.official_submission_id
            left join competition.leaderboard_latest lb
              on lb.participant_id=p.id
             and lb.scenario_id=$2
             and lb.window_type='cumulative'
            where ps.scenario_id=$2 and ps.status='active' and p.kind='student'
              and p.cohort_code=$3
            order by lb.rank nulls last,
                     (s.status='accepted') desc,
                     api_key_active desc,
                     p.display_name
            """,
            cycle["id"],
            cycle["scenario_id"],
            identity.cohort_code,
        )
    mode = "scored" if any(row["accuracy"] is not None for row in rows) else "integration"
    return {
        "mode": mode,
        "cycle": {
            "cycle_id": cycle["public_id"],
            "state": cycle["state"],
            "opens_at": cycle["opens_at"],
            "closes_at": cycle["closes_at"],
            "expected_predictions": cycle["expected_predictions"],
        },
        "data": [dict(row) for row in rows],
        "count": len(rows),
    }
