import asyncio
from datetime import datetime, timezone

import pytest
from fastapi import HTTPException

from app.portal import (
    PortalIdentity,
    cohort_board,
    identity_hash,
    login,
    normalize_identity,
    normalize_preferred_name,
    rotate_api_key,
    session_hash,
)
from app.admin import validate_avatar_records


def test_avatar_map_requires_unique_valid_assignments() -> None:
    assert validate_avatar_records(
        [
            {"participant_id": "stu_" + "a" * 32, "avatar_index": 0},
            {"participant_id": "stu_" + "b" * 32, "avatar_index": 35},
        ]
    ) == [("stu_" + "a" * 32, 0), ("stu_" + "b" * 32, 35)]

    with pytest.raises(ValueError, match="unique within the cohort"):
        validate_avatar_records(
            [
                {"participant_id": "stu_" + "a" * 32, "avatar_index": 7},
                {"participant_id": "stu_" + "b" * 32, "avatar_index": 7},
            ]
        )

    with pytest.raises(ValueError, match="between 0 and 35"):
        validate_avatar_records(
            [{"participant_id": "stu_" + "a" * 32, "avatar_index": 36}]
        )


def test_cohort_board_has_an_empty_timeline_before_first_cycle() -> None:
    class Context:
        def __init__(self, value=None):
            self.value = value

        async def __aenter__(self):
            return self.value

        async def __aexit__(self, *_args):
            return False

    class Connection:
        async def fetchrow(self, _query, *_args):
            return None

    class Pool:
        def acquire(self):
            return Context(Connection())

    identity = PortalIdentity(7, "stu_test", "Test", "QA", "A", "Test", "hash")
    result = asyncio.run(cohort_board(Pool(), identity))

    assert result == {
        "mode": "integration",
        "cycle": None,
        "data": [],
        "timeline": [],
        "count": 0,
    }


def test_identity_normalization_accepts_accents_and_spacing() -> None:
    assert normalize_identity("USER@EST.UEXTERNADO.EDU.CO ", "email") == "user@est.uexternado.edu.co"
    assert normalize_identity("1.023.456.789", "student_code") == "1023456789"


def test_identity_hash_is_scoped_and_peppered() -> None:
    email = identity_hash("123456", "email", "pepper-one")
    document = identity_hash("123456", "student_code", "pepper-one")
    changed_pepper = identity_hash("123456", "email", "pepper-two")
    assert email != document
    assert email != changed_pepper
    assert b"123456" not in email


def test_session_hash_does_not_contain_token() -> None:
    raw = "a-private-session-token"
    assert session_hash(raw) != raw
    assert len(session_hash(raw)) == 64


def test_preferred_name_preserves_human_spelling() -> None:
    assert normalize_preferred_name("  Mafe   🚀  ") == "Mafe 🚀"


def test_login_uses_email_and_document_not_name() -> None:
    class Context:
        def __init__(self, value=None):
            self.value = value

        async def __aenter__(self):
            return self.value

        async def __aexit__(self, *_args):
            return False

    class Connection:
        def __init__(self):
            self.fetch_args = None
            self.session_args = None

        def transaction(self):
            return Context()

        async def fetchrow(self, query, *args):
            assert "login_name_hash" not in query
            self.fetch_args = args
            return {
                "id": 7,
                "public_id": "stu_test",
                "display_name": "Nombre oficial de matrícula",
                "cohort_code": "VIS2-2026II",
                "section_code": "A",
            }

        async def execute(self, query, *args):
            if "insert into competition.portal_sessions" in query:
                self.session_args = args

    class Pool:
        def __init__(self, connection):
            self.connection = connection

        def acquire(self):
            return Context(self.connection)

    connection = Connection()
    identity, _, _ = asyncio.run(
        login(
            Pool(connection),
            name="Un apodo completamente distinto",
            email="student@est.uexternado.edu.co",
            student_code="1.234.567",
            pepper="test-pepper",
            session_hours=8,
        )
    )

    assert len(connection.fetch_args) == 2
    assert connection.session_args[-1] == "Un apodo completamente distinto"
    assert identity.display_name == "Nombre oficial de matrícula"
    assert identity.preferred_name == "Un apodo completamente distinto"


def test_rotation_revokes_previous_key_and_returns_a_new_secret_once() -> None:
    class Context:
        def __init__(self, value=None):
            self.value = value

        async def __aenter__(self):
            return self.value

        async def __aexit__(self, *_args):
            return False

    class Connection:
        def __init__(self):
            self.queries = []

        def transaction(self):
            return Context()

        async def fetchrow(self, query, *_args):
            self.queries.append(query)
            return {"id": 11, "key_prefix": "ptm_live_previous"}

        async def fetchval(self, query, *_args):
            self.queries.append(query)
            if "select count(*)" in query:
                return 1
            return datetime(2026, 9, 18, tzinfo=timezone.utc)

        async def execute(self, query, *_args):
            self.queries.append(query)

    class Pool:
        def __init__(self, connection):
            self.connection = connection

        def acquire(self):
            return Context(self.connection)

    connection = Connection()
    identity = PortalIdentity(7, "stu_test", "Test", "QA", "A", "Test", "hash")
    result = asyncio.run(rotate_api_key(Pool(connection), identity, 4))

    assert result["api_key"].startswith("ptm_live_")
    assert result["key_prefix"] != "ptm_live_previous"
    assert result["revoked_key_prefix"] == "ptm_live_previous"
    assert result["shown_once"] is True
    revoke_index = next(
        index
        for index, query in enumerate(connection.queries)
        if "update competition.api_keys set revoked_at" in query
    )
    insert_index = next(
        index
        for index, query in enumerate(connection.queries)
        if "insert into competition.api_keys" in query
    )
    assert revoke_index < insert_index
    assert any("api_key.rotated" in query for query in connection.queries)


def test_rotation_is_rate_limited_before_revoking_the_active_key() -> None:
    class Context:
        def __init__(self, value=None):
            self.value = value

        async def __aenter__(self):
            return self.value

        async def __aexit__(self, *_args):
            return False

    class Connection:
        def __init__(self):
            self.queries = []

        def transaction(self):
            return Context()

        async def fetchrow(self, query, *_args):
            self.queries.append(query)
            return {"id": 11, "key_prefix": "ptm_live_previous"}

        async def fetchval(self, query, *_args):
            self.queries.append(query)
            return 4

        async def execute(self, query, *_args):
            self.queries.append(query)

    class Pool:
        def __init__(self, connection):
            self.connection = connection

        def acquire(self):
            return Context(self.connection)

    connection = Connection()
    identity = PortalIdentity(7, "stu_test", "Test", "QA", "A", "Test", "hash")
    with pytest.raises(HTTPException) as raised:
        asyncio.run(rotate_api_key(Pool(connection), identity, 4))

    assert raised.value.status_code == 429
    assert raised.value.detail["code"] == "api_key_rotation_rate_limited"
    assert not any(
        "update competition.api_keys set revoked_at" in query
        for query in connection.queries
    )
