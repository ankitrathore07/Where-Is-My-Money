"""Small immutable contracts for transaction statement profiles."""

from collections.abc import Callable
from dataclasses import dataclass

from app.imports.types import ColumnMapping, CsvDocument


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


@dataclass(frozen=True)
class ProviderPdfProfile:
    key: str
    institution_key: str
    account_types: frozenset[str]
    matches: Callable[[str], bool]
    parse: Callable[[str], CsvDocument]


@dataclass(frozen=True)
class ProviderDocumentResolution:
    profile_key: str
    document: CsvDocument
