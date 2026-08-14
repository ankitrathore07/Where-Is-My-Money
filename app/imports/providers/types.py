"""Small immutable contracts for transaction statement profiles."""

from dataclasses import dataclass

from app.imports.types import ColumnMapping


@dataclass(frozen=True)
class ProviderProfile:
    key: str
    institution_key: str
    account_types: frozenset[str]
    suffixes: frozenset[str]
    required_headers: frozenset[str]
    mapping: ColumnMapping


@dataclass(frozen=True)
class ProviderResolution:
    profile_key: str
    mapping: ColumnMapping | None
    recognized: bool
