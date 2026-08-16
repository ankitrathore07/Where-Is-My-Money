from collections.abc import Mapping

import pytest
from itsdangerous import URLSafeTimedSerializer
from itsdangerous.timed import TimestampSigner

from app.rules import application_tokens
from app.rules.application_tokens import (
    APPLICATION_TOKEN_SALT,
    ApplicationTokenPayload,
    RuleApplicationTokenError,
    create_application_token,
    load_application_token,
)

SECRET = "application-secret"
DIGEST = "a" * 64


def preview_payload(
    *,
    selected_transaction_ids: tuple[int, ...] = (11, 3),
    state_digest: str = DIGEST,
    normalized_filters: Mapping[str, object] | None = None,
) -> ApplicationTokenPayload:
    return ApplicationTokenPayload(
        workspace_id=7,
        merchant_rule_id=13,
        rule_lock_version=2,
        selected_transaction_ids=selected_transaction_ids,
        state_digest=state_digest,
        normalized_filters=dict(
            normalized_filters
            if normalized_filters is not None
            else {
                "direction": "expense",
                "account_id": 5,
                "category_id": None,
                "date_from": "2026-01-01",
            }
        ),
    )


def _signed_payload(**overrides: object) -> str:
    raw: dict[str, object] = {
        "v": 1,
        "workspace_id": 7,
        "merchant_rule_id": 13,
        "rule_lock_version": 2,
        "selected_transaction_ids": [3, 11],
        "state_digest": DIGEST,
        "normalized_filters": {"account_id": 5, "direction": "expense"},
    }
    raw.update(overrides)
    return URLSafeTimedSerializer(SECRET, salt=APPLICATION_TOKEN_SALT).dumps(raw)


def test_application_token_round_trips_canonical_payload_and_rejects_tampering() -> None:
    """Break if signed state can be changed or semantically identical inputs stay unordered."""
    token = create_application_token(SECRET, preview_payload(state_digest=DIGEST))
    payload = load_application_token(SECRET, token)

    assert payload == ApplicationTokenPayload(
        workspace_id=7,
        merchant_rule_id=13,
        rule_lock_version=2,
        selected_transaction_ids=(3, 11),
        state_digest=DIGEST,
        normalized_filters={
            "account_id": 5,
            "category_id": None,
            "date_from": "2026-01-01",
            "direction": "expense",
        },
    )
    with pytest.raises(RuleApplicationTokenError):
        load_application_token(SECRET, token + "tampered")


def test_application_selection_is_canonical_and_contains_only_approved_state() -> None:
    """Break if audit selection can retain browser order or any state beyond filters and IDs."""
    selection = application_tokens.canonical_application_selection(
        preview_payload(
            selected_transaction_ids=(11, 3),
            normalized_filters={"direction": "expense", "account_id": 5},
        )
    )

    assert selection == {
        "normalized_filters": {"account_id": 5, "direction": "expense"},
        "selected_transaction_ids": (3, 11),
    }


@pytest.mark.parametrize(
    "sensitive_filters",
    [
        {"description": "PRIVATE MERCHANT"},
        {"amount_cents": -1_250},
        {"token": "signed-preview-token"},
        {"condition_json": {"field": "description"}},
    ],
)
def test_application_selection_rejects_sensitive_or_nested_state(
    sensitive_filters: dict[str, object],
) -> None:
    """Break if prohibited financial, token, or condition data can enter audit selection."""
    with pytest.raises(RuleApplicationTokenError):
        application_tokens.canonical_application_selection(
            preview_payload(normalized_filters=sensitive_filters)
        )


def test_application_token_canonicalization_is_deterministic(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Break if filter insertion order or selected-ID order changes the signed payload."""
    monkeypatch.setattr(TimestampSigner, "get_timestamp", lambda _signer: 1_786_752_000)
    first = create_application_token(
        SECRET,
        preview_payload(
            selected_transaction_ids=(11, 3),
            normalized_filters={"direction": "expense", "account_id": 5},
        ),
    )
    second = create_application_token(
        SECRET,
        preview_payload(
            selected_transaction_ids=(3, 11),
            normalized_filters={"account_id": 5, "direction": "expense"},
        ),
    )

    assert first == second


@pytest.mark.parametrize(
    ("field", "invalid_value"),
    [
        ("v", 2),
        ("workspace_id", True),
        ("workspace_id", 0),
        ("merchant_rule_id", False),
        ("merchant_rule_id", -1),
        ("rule_lock_version", True),
        ("rule_lock_version", 0),
        ("selected_transaction_ids", [1, True]),
        ("selected_transaction_ids", [1, 1]),
        ("selected_transaction_ids", [0]),
        ("selected_transaction_ids", list(range(1, 502))),
        ("state_digest", "ABC" + "0" * 61),
        ("state_digest", "abc"),
        ("normalized_filters", {"account_id": True}),
        ("normalized_filters", {"account_id": "5"}),
        ("normalized_filters", {"date_from": 20260815}),
        ("normalized_filters", {"date_to": "08/15/2026"}),
        ("normalized_filters", {"direction": "sideways"}),
        ("normalized_filters", {"description": "must not enter audit selection"}),
        ("normalized_filters", {"amount": 1.5}),
        ("normalized_filters", {"nested": {"condition": "secret"}}),
    ],
)
def test_application_token_rejects_wrong_version_or_exact_payload_types(
    field: str, invalid_value: object
) -> None:
    """Break if JSON coercion lets malformed or unbounded application state through."""
    with pytest.raises(RuleApplicationTokenError):
        load_application_token(SECRET, _signed_payload(**{field: invalid_value}))


def test_application_token_accepts_exactly_five_hundred_distinct_positive_ids() -> None:
    """Break if the documented safe application boundary is rejected or exceeded."""
    selected = tuple(range(500, 0, -1))
    payload = load_application_token(
        SECRET,
        create_application_token(
            SECRET,
            preview_payload(selected_transaction_ids=selected, normalized_filters={}),
        ),
    )
    assert payload.selected_transaction_ids == tuple(range(1, 501))


def test_application_token_creation_rejects_invalid_typed_input() -> None:
    """Break if trusted server code can emit tokens that the loader itself rejects."""
    with pytest.raises(RuleApplicationTokenError):
        create_application_token(
            SECRET,
            preview_payload(selected_transaction_ids=(1, True)),  # type: ignore[arg-type]
        )


def test_application_token_rejects_expiry(monkeypatch: pytest.MonkeyPatch) -> None:
    """Break if a preview remains actionable beyond its one-hour confirmation window."""
    token = create_application_token(SECRET, preview_payload())
    current_timestamp = TimestampSigner.get_timestamp
    monkeypatch.setattr(
        TimestampSigner,
        "get_timestamp",
        lambda signer: current_timestamp(signer) + 3_601,
    )

    with pytest.raises(RuleApplicationTokenError):
        load_application_token(SECRET, token)
