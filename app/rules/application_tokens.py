"""Versioned signed state for bounded historical rule application previews."""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date
from types import MappingProxyType

from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer
from sqlalchemy import JSON
from sqlalchemy.types import TypeDecorator

APPLICATION_TOKEN_SALT = "where-is-my-money-rule-application"
APPLICATION_TOKEN_MAX_AGE = 3600
APPLICATION_TOKEN_VERSION = 1
MAX_SELECTED_TRANSACTION_IDS = 500

_DIGEST_PATTERN = re.compile(r"[0-9a-f]{64}")
_FILTER_KEYS = {"account_id", "category_id", "date_from", "date_to", "direction"}
_DIRECTIONS = {"all", "expense", "income", "zero"}
_SELECTION_KEYS = {"normalized_filters", "selected_transaction_ids"}
_PAYLOAD_KEYS = {
    "v",
    "workspace_id",
    "merchant_rule_id",
    "rule_lock_version",
    "selected_transaction_ids",
    "state_digest",
    "normalized_filters",
}

type NormalizedFilterValue = str | int | None


class RuleApplicationTokenError(ValueError):
    """Raised when historical application preview state cannot be trusted."""


def validate_application_digest(value: object) -> str:
    """Return an exact lowercase SHA-256-style digest or reject the value."""
    if type(value) is not str or _DIGEST_PATTERN.fullmatch(value) is None:
        raise RuleApplicationTokenError(
            "Application preview could not be verified; reload and try again."
        )
    return value


@dataclass(frozen=True)
class ApplicationTokenPayload:
    workspace_id: int
    merchant_rule_id: int
    rule_lock_version: int
    selected_transaction_ids: tuple[int, ...]
    state_digest: str
    normalized_filters: Mapping[str, NormalizedFilterValue]


class _FrozenMapping(Mapping[str, object]):
    """Small immutable mapping used for canonical and hydrated audit selections."""

    __slots__ = ("_values",)

    def __init__(self, values: Mapping[str, object]) -> None:
        self._values = MappingProxyType(dict(values))

    def __getitem__(self, key: str) -> object:
        return self._values[key]

    def __iter__(self):
        return iter(self._values)

    def __len__(self) -> int:
        return len(self._values)

    def __eq__(self, other: object) -> bool:
        return isinstance(other, Mapping) and dict(self.items()) == dict(other.items())


class _CanonicalApplicationSelection(_FrozenMapping):
    """Deeply immutable representation returned by the persistence boundary."""


def _positive_int(value: object) -> bool:
    return type(value) is int and value > 0


def _canonical_filters(value: object) -> dict[str, NormalizedFilterValue] | None:
    if not isinstance(value, Mapping):
        return None
    canonical: dict[str, NormalizedFilterValue] = {}
    for key, filter_value in value.items():
        if type(key) is not str or key not in _FILTER_KEYS:
            return None
        if key in {"account_id", "category_id"} and not (
            filter_value is None or _positive_int(filter_value)
        ):
            return None
        if key in {"date_from", "date_to"} and filter_value is not None:
            if type(filter_value) is not str:
                return None
            try:
                if date.fromisoformat(filter_value).isoformat() != filter_value:
                    return None
            except ValueError:
                return None
        if key == "direction" and not (
            filter_value is None or (type(filter_value) is str and filter_value in _DIRECTIONS)
        ):
            return None
        canonical[key] = filter_value
    return dict(sorted(canonical.items()))


def _canonicalize_application_selection(value: object) -> _CanonicalApplicationSelection:
    if not isinstance(value, Mapping) or set(value) != _SELECTION_KEYS:
        raise RuleApplicationTokenError(
            "Application preview could not be verified; reload and try again."
        )
    filters = _canonical_filters(value["normalized_filters"])
    selected_ids = value["selected_transaction_ids"]
    valid = (
        filters is not None
        and type(selected_ids) in {list, tuple}
        and len(selected_ids) <= MAX_SELECTED_TRANSACTION_IDS
        and all(_positive_int(transaction_id) for transaction_id in selected_ids)
        and len(selected_ids) == len(set(selected_ids))
        and tuple(selected_ids) == tuple(sorted(selected_ids))
    )
    if not valid:
        raise RuleApplicationTokenError(
            "Application preview could not be verified; reload and try again."
        )
    return _CanonicalApplicationSelection(
        {
            "normalized_filters": _FrozenMapping(filters),
            "selected_transaction_ids": tuple(selected_ids),
        }
    )


class ApplicationSelectionJSON(TypeDecorator):
    """Validate every audit JSON bind and deeply freeze every hydrated result."""

    impl = JSON
    cache_ok = True

    def process_bind_param(self, value: object, _dialect: object) -> dict[str, object]:
        canonical = _canonicalize_application_selection(value)
        return {
            "normalized_filters": dict(canonical["normalized_filters"]),
            "selected_transaction_ids": list(canonical["selected_transaction_ids"]),
        }

    def process_result_value(
        self, value: object, _dialect: object
    ) -> _CanonicalApplicationSelection | None:
        if value is None:
            return None
        return _canonicalize_application_selection(value)


def _validated_payload(raw: object) -> ApplicationTokenPayload:
    if type(raw) is not dict or set(raw) != _PAYLOAD_KEYS:
        raise RuleApplicationTokenError(
            "Application preview could not be verified; reload and try again."
        )

    version = raw["v"]
    workspace_id = raw["workspace_id"]
    merchant_rule_id = raw["merchant_rule_id"]
    rule_lock_version = raw["rule_lock_version"]
    selected_ids = raw["selected_transaction_ids"]
    state_digest = validate_application_digest(raw["state_digest"])
    filters = _canonical_filters(raw["normalized_filters"])
    valid = (
        type(version) is int
        and version == APPLICATION_TOKEN_VERSION
        and _positive_int(workspace_id)
        and _positive_int(merchant_rule_id)
        and _positive_int(rule_lock_version)
        and type(selected_ids) is list
        and len(selected_ids) <= MAX_SELECTED_TRANSACTION_IDS
        and all(_positive_int(transaction_id) for transaction_id in selected_ids)
        and len(selected_ids) == len(set(selected_ids))
        and filters is not None
    )
    if not valid:
        raise RuleApplicationTokenError(
            "Application preview could not be verified; reload and try again."
        )
    return ApplicationTokenPayload(
        workspace_id=workspace_id,
        merchant_rule_id=merchant_rule_id,
        rule_lock_version=rule_lock_version,
        selected_transaction_ids=tuple(sorted(selected_ids)),
        state_digest=state_digest,
        normalized_filters=filters,
    )


def canonical_application_selection(
    payload: ApplicationTokenPayload,
) -> Mapping[str, object]:
    """Return the only selection JSON shape supported by application audit persistence."""
    if type(payload) is not ApplicationTokenPayload:
        raise RuleApplicationTokenError(
            "Application preview could not be verified; reload and try again."
        )
    validated = _validated_payload(
        {
            "v": APPLICATION_TOKEN_VERSION,
            "workspace_id": payload.workspace_id,
            "merchant_rule_id": payload.merchant_rule_id,
            "rule_lock_version": payload.rule_lock_version,
            "selected_transaction_ids": list(payload.selected_transaction_ids),
            "state_digest": payload.state_digest,
            "normalized_filters": payload.normalized_filters,
        }
    )
    return _canonicalize_application_selection(
        {
            "normalized_filters": validated.normalized_filters,
            "selected_transaction_ids": validated.selected_transaction_ids,
        }
    )


def create_application_token(secret_key: str, payload: ApplicationTokenPayload) -> str:
    """Validate, canonicalize, and sign one bounded historical preview payload."""
    if (
        type(secret_key) is not str
        or not secret_key
        or type(payload) is not ApplicationTokenPayload
    ):
        raise RuleApplicationTokenError(
            "Application preview could not be verified; reload and try again."
        )
    if type(payload.selected_transaction_ids) is not tuple:
        raise RuleApplicationTokenError(
            "Application preview could not be verified; reload and try again."
        )
    validated = _validated_payload(
        {
            "v": APPLICATION_TOKEN_VERSION,
            "workspace_id": payload.workspace_id,
            "merchant_rule_id": payload.merchant_rule_id,
            "rule_lock_version": payload.rule_lock_version,
            "selected_transaction_ids": list(payload.selected_transaction_ids),
            "state_digest": payload.state_digest,
            "normalized_filters": payload.normalized_filters,
        }
    )
    serializer = URLSafeTimedSerializer(secret_key, salt=APPLICATION_TOKEN_SALT)
    return serializer.dumps(
        {
            "v": APPLICATION_TOKEN_VERSION,
            "workspace_id": validated.workspace_id,
            "merchant_rule_id": validated.merchant_rule_id,
            "rule_lock_version": validated.rule_lock_version,
            "selected_transaction_ids": list(validated.selected_transaction_ids),
            "state_digest": validated.state_digest,
            "normalized_filters": validated.normalized_filters,
        }
    )


def load_application_token(secret_key: str, token: str) -> ApplicationTokenPayload:
    """Load a valid one-hour application preview or reject it without partial trust."""
    if type(secret_key) is not str or not secret_key or type(token) is not str:
        raise RuleApplicationTokenError(
            "Application preview could not be verified; reload and try again."
        )
    serializer = URLSafeTimedSerializer(secret_key, salt=APPLICATION_TOKEN_SALT)
    try:
        raw = serializer.loads(token, max_age=APPLICATION_TOKEN_MAX_AGE)
    except (BadSignature, SignatureExpired, TypeError) as exc:
        raise RuleApplicationTokenError(
            "Application preview could not be verified; reload and try again."
        ) from exc
    return _validated_payload(raw)
