from __future__ import annotations

import hashlib
import json
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone

import asyncpg
from fastapi import HTTPException

from app.contracts import SubmissionInput
from app.security import split_api_key, verify_secret


@dataclass(frozen=True)
class ParticipantIdentity:
    participant_id: int
    public_id: str
    display_name: str
    kind: str
    key_id: int


async def authenticate(pool: asyncpg.Pool, authorization: str | None) -> ParticipantIdentity:
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail={"code": "invalid_api_key", "message": "Bearer API key required"})
    parsed = split_api_key(authorization[7:].strip())
    if parsed is None:
        raise HTTPException(status_code=401, detail={"code": "invalid_api_key", "message": "Invalid API key"})
    prefix, secret = parsed
    async with pool.acquire() as connection:
        row = await connection.fetchrow(
            """
            select k.id as key_id, k.secret_hash, p.id as participant_id,
                   p.public_id, p.display_name, p.kind
            from competition.api_keys k
            join competition.participants p on p.id = k.participant_id
            where k.key_prefix = $1 and k.revoked_at is null
            """,
            prefix,
        )
        if row is None or not verify_secret(secret, row["secret_hash"]):
            raise HTTPException(status_code=401, detail={"code": "invalid_api_key", "message": "Invalid API key"})
        await connection.execute(
            "update competition.api_keys set last_used_at = now() where id = $1", row["key_id"]
        )
    return ParticipantIdentity(
        participant_id=row["participant_id"], public_id=row["public_id"],
        display_name=row["display_name"], kind=row["kind"], key_id=row["key_id"]
    )


def payload_digest(payload: SubmissionInput) -> str:
    canonical = json.dumps(payload.model_dump(mode="json"), sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode()).hexdigest()


def _receipt(row: asyncpg.Record, expected: int, replaced: str | None = None) -> dict[str, object]:
    return {
        "submission_id": row["public_id"], "status": row["status"],
        "attempt": row["attempt_number"], "received_at": row["received_at"],
        "closes_at": row["closes_at"], "predictions_received": expected,
        "expected_predictions": expected, "is_official": row["status"] == "accepted",
        "payload_hash": f"sha256:{row['payload_hash']}",
        "replaced_submission_id": replaced,
    }


async def submit(
    pool: asyncpg.Pool,
    identity: ParticipantIdentity,
    payload: SubmissionInput,
    idempotency_key: str,
    request_id: str,
    max_attempts: int,
) -> tuple[dict[str, object], bool]:
    digest = payload_digest(payload)
    async with pool.acquire() as connection:
        async with connection.transaction():
            cycle = await connection.fetchrow(
                """
                select id, public_id, scenario_id, data_cutoff, closes_at, state
                from competition.forecast_cycles where public_id = $1
                """,
                payload.cycle_id,
            )
            if cycle is None:
                raise HTTPException(status_code=404, detail={"code": "cycle_not_found", "message": "Forecast cycle not found"})
            await connection.execute(
                "select pg_advisory_xact_lock($1::integer,$2::integer)",
                cycle["id"],
                identity.participant_id,
            )
            server_now = await connection.fetchval("select now()")
            if cycle["state"] != "open" or server_now >= cycle["closes_at"]:
                raise HTTPException(status_code=409, detail={"code": "cycle_closed", "message": "Forecast cycle is closed"})
            active = await connection.fetchval(
                """
                select exists(
                  select 1 from competition.participant_scenarios
                  where participant_id=$1 and scenario_id=$2 and status='active'
                )
                """,
                identity.participant_id, cycle["scenario_id"],
            )
            if not active:
                raise HTTPException(status_code=403, detail={"code": "participant_inactive", "message": "Participant is not active in this scenario"})
            if payload.data_cutoff != cycle["data_cutoff"]:
                raise HTTPException(status_code=422, detail={"code": "invalid_data_cutoff", "message": "data_cutoff must match the cycle contract"})
            if payload.model.training_data_end and payload.model.training_data_end > payload.data_cutoff:
                raise HTTPException(status_code=422, detail={"code": "invalid_training_data_end", "message": "training_data_end cannot be after data_cutoff"})

            previous_by_key = await connection.fetchrow(
                """
                select s.*, c.closes_at from competition.submissions s
                join competition.forecast_cycles c on c.id=s.cycle_id
                where s.participant_id=$1 and s.idempotency_key=$2
                """,
                identity.participant_id, idempotency_key,
            )
            if previous_by_key:
                if previous_by_key["payload_hash"] != digest:
                    raise HTTPException(status_code=409, detail={"code": "idempotency_conflict", "message": "Idempotency-Key was already used with different content"})
                count = await connection.fetchval("select count(*) from competition.predictions where submission_id=$1", previous_by_key["id"])
                return _receipt(previous_by_key, count), True

            targets = await connection.fetch(
                "select station_id, target_at from competition.cycle_targets where cycle_id=$1",
                cycle["id"],
            )
            expected = {(row["station_id"], row["target_at"]) for row in targets}
            received = {(item.station_id, item.target_at) for item in payload.predictions}
            if len(received) != len(payload.predictions) or received != expected:
                missing = sorted(f"{s}@{t.isoformat()}" for s, t in expected - received)[:10]
                extra = sorted(f"{s}@{t.isoformat()}" for s, t in received - expected)[:10]
                raise HTTPException(
                    status_code=422,
                    detail={"code": "invalid_target_set", "message": "Predictions must match the cycle targets exactly", "expected": len(expected), "received": len(payload.predictions), "missing": missing, "extra": extra},
                )
            duplicate = await connection.fetchrow(
                """
                select s.*, c.closes_at from competition.submissions s
                join competition.forecast_cycles c on c.id=s.cycle_id
                where s.participant_id=$1 and s.cycle_id=$2 and s.payload_hash=$3
                """,
                identity.participant_id, cycle["id"], digest,
            )
            if duplicate:
                return _receipt(duplicate, len(expected)), True
            attempt = await connection.fetchval(
                "select count(*) + 1 from competition.submissions where participant_id=$1 and cycle_id=$2",
                identity.participant_id, cycle["id"],
            )
            if attempt > max_attempts:
                raise HTTPException(status_code=409, detail={"code": "attempt_limit_reached", "message": f"Maximum {max_attempts} attempts per cycle"})

            current = await connection.fetchrow(
                """
                select s.id, s.public_id from competition.cycle_entries e
                join competition.submissions s on s.id=e.official_submission_id
                where e.cycle_id=$1 and e.participant_id=$2
                """,
                cycle["id"], identity.participant_id,
            )
            if current:
                await connection.execute("update competition.submissions set status='superseded' where id=$1", current["id"])
            public_id = f"sub_{uuid.uuid4().hex}"
            row = await connection.fetchrow(
                """
                insert into competition.submissions
                  (public_id, participant_id, cycle_id, attempt_number, data_cutoff,
                   model_version, git_commit, payload_hash, status, schema_version,
                   client_run_id, idempotency_key, trained_at, training_data_end, request_id)
                values ($1,$2,$3,$4,$5,$6,$7,$8,'accepted',$9,$10,$11,$12,$13,$14)
                returning *, $15::timestamptz as closes_at
                """,
                public_id, identity.participant_id, cycle["id"], attempt,
                payload.data_cutoff, payload.model.version, payload.model.git_commit,
                digest, payload.schema_version, payload.client_run_id, idempotency_key,
                payload.model.trained_at, payload.model.training_data_end, request_id,
                cycle["closes_at"],
            )
            await connection.executemany(
                "insert into competition.predictions (submission_id, station_id, target_at, predicted_value) values ($1,$2,$3,$4)",
                [(row["id"], item.station_id, item.target_at, item.value) for item in payload.predictions],
            )
            await connection.execute(
                """
                insert into competition.cycle_entries
                  (cycle_id, participant_id, official_submission_id, first_submitted_at, latest_submitted_at)
                values ($1,$2,$3,$4,$4)
                on conflict (cycle_id,participant_id) do update
                set official_submission_id=excluded.official_submission_id,
                    latest_submitted_at=excluded.latest_submitted_at
                """,
                cycle["id"], identity.participant_id, row["id"], row["received_at"],
            )
            await connection.execute(
                """
                insert into ops.audit_events
                  (actor_type,actor_id,action,entity_type,entity_id,request_id,metadata)
                values ('participant',$1,'submission.accepted','submission',$2,$3,$4::jsonb)
                """,
                identity.public_id, public_id, request_id,
                json.dumps({"cycle_id": payload.cycle_id, "attempt": attempt, "predictions": len(expected)}),
            )
            return _receipt(row, len(expected), current["public_id"] if current else None), False


async def receipt(pool: asyncpg.Pool, identity: ParticipantIdentity, public_id: str) -> dict[str, object]:
    async with pool.acquire() as connection:
        row = await connection.fetchrow(
            """
            select s.*, c.public_id as cycle_public_id, c.closes_at,
                   (select count(*) from competition.predictions p where p.submission_id=s.id) as prediction_count,
                   e.official_submission_id = s.id as is_official
            from competition.submissions s
            join competition.forecast_cycles c on c.id=s.cycle_id
            left join competition.cycle_entries e on e.cycle_id=s.cycle_id and e.participant_id=s.participant_id
            where s.public_id=$1 and s.participant_id=$2
            """,
            public_id, identity.participant_id,
        )
    if row is None:
        raise HTTPException(status_code=404, detail={"code": "submission_not_found", "message": "Submission not found"})
    return {
        "submission_id": row["public_id"], "cycle_id": row["cycle_public_id"],
        "status": row["status"], "attempt": row["attempt_number"],
        "received_at": row["received_at"], "closes_at": row["closes_at"],
        "predictions_received": row["prediction_count"], "is_official": bool(row["is_official"]),
        "payload_hash": f"sha256:{row['payload_hash']}",
    }
