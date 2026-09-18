from app.portal import identity_hash, normalize_identity, session_hash


def test_identity_normalization_accepts_accents_and_spacing() -> None:
    assert normalize_identity("  María   José  ", "name") == "maria jose"
    assert normalize_identity("USER@EST.UEXTERNADO.EDU.CO ", "email") == "user@est.uexternado.edu.co"
    assert normalize_identity("1.023.456.789", "student_code") == "1023456789"


def test_identity_hash_is_scoped_and_peppered() -> None:
    name = identity_hash("María José", "name", "pepper-one")
    email = identity_hash("María José", "email", "pepper-one")
    changed_pepper = identity_hash("María José", "name", "pepper-two")
    assert name != email
    assert name != changed_pepper
    assert b"Mar" not in name


def test_session_hash_does_not_contain_token() -> None:
    raw = "a-private-session-token"
    assert session_hash(raw) != raw
    assert len(session_hash(raw)) == 64
