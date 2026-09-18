import asyncio

from app.portal import (
    identity_hash,
    login,
    normalize_identity,
    normalize_preferred_name,
    session_hash,
)


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
