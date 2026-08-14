"""Immutable institution identities for accounts and parser selection."""

from dataclasses import dataclass
from types import MappingProxyType


@dataclass(frozen=True)
class InstitutionDefinition:
    key: str
    label: str


INSTITUTIONS = (
    InstitutionDefinition("chase", "Chase"),
    InstitutionDefinition("bank_of_america", "Bank of America"),
    InstitutionDefinition("citi", "Citi"),
    InstitutionDefinition("capital_one", "Capital One"),
    InstitutionDefinition("american_express", "American Express"),
    InstitutionDefinition("discover", "Discover"),
    InstitutionDefinition("wells_fargo", "Wells Fargo"),
    InstitutionDefinition("other", "Other / manual mapping"),
)
_INSTITUTION_BY_KEY = MappingProxyType({item.key: item for item in INSTITUTIONS})


def get_institution(key: str) -> InstitutionDefinition | None:
    """Return a known institution by its stable persisted key."""
    return _INSTITUTION_BY_KEY.get(key)


def institution_options() -> tuple[InstitutionDefinition, ...]:
    """Return known institutions in their stable user-facing order."""
    return INSTITUTIONS
