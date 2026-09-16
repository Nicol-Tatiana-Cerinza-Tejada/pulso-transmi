import pytest
from pydantic import ValidationError

from app.competition import payload_digest
from app.contracts import SubmissionInput
from app.security import generate_api_key, split_api_key, verify_secret


def valid_payload() -> dict:
    return {
        "schema_version": "1.0",
        "cycle_id": "cyc_p1_20260916T150000Z",
        "client_run_id": "gha-123-1",
        "data_cutoff": "2026-09-16T10:00:00-05:00",
        "model": {
            "version": "xgb:3.1",
            "trained_at": "2026-09-16T09:55:00-05:00",
            "training_data_end": "2026-09-16T10:00:00-05:00",
            "git_commit": "abcdef1234567",
        },
        "predictions": [
            {
                "station_id": "02300",
                "target_at": "2026-09-16T10:15:00-05:00",
                "value": 321.5,
            }
        ],
    }


def test_api_key_is_stored_as_verifiable_hash() -> None:
    raw, prefix, encoded = generate_api_key()
    assert split_api_key(raw) is not None
    assert split_api_key(raw)[0] == prefix
    assert raw not in encoded
    assert verify_secret(raw.split(".", 1)[1], encoded)
    assert not verify_secret("wrong", encoded)


def test_submission_rejects_unknown_fields_and_bad_values() -> None:
    payload = valid_payload()
    payload["student_name"] = "identity-must-not-come-from-body"
    with pytest.raises(ValidationError):
        SubmissionInput.model_validate(payload)

    payload = valid_payload()
    payload["predictions"][0]["value"] = float("nan")
    with pytest.raises(ValidationError):
        SubmissionInput.model_validate(payload)


def test_submission_rejects_naive_timestamps() -> None:
    payload = valid_payload()
    payload["data_cutoff"] = "2026-09-16T10:00:00"
    with pytest.raises(ValidationError):
        SubmissionInput.model_validate(payload)


def test_payload_digest_is_canonical() -> None:
    first = SubmissionInput.model_validate(valid_payload())
    second = SubmissionInput.model_validate(valid_payload())
    assert payload_digest(first) == payload_digest(second)
    assert first.data_cutoff.utcoffset() is not None
