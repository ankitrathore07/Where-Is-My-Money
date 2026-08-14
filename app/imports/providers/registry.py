"""Explicit provider-profile resolution from a selected account."""

from app.imports.providers.chase import CHASE_PROVIDER_PROFILES
from app.imports.providers.types import ProviderProfile, ProviderResolution

PROVIDER_PROFILES: tuple[ProviderProfile, ...] = CHASE_PROVIDER_PROFILES


def _normalized_headers(headers: tuple[str, ...]) -> frozenset[str]:
    return frozenset(header.removeprefix("\ufeff").strip() for header in headers)


def resolve_provider_profile(
    institution_key: str | None,
    account_type: str,
    suffix: str,
    headers: tuple[str, ...],
) -> ProviderResolution:
    """Resolve only profiles belonging to the account's selected institution."""
    normalized_suffix = suffix.casefold()
    normalized_headers = _normalized_headers(headers)
    for profile in PROVIDER_PROFILES:
        if (
            profile.institution_key == institution_key
            and account_type in profile.account_types
            and normalized_suffix in profile.suffixes
            and profile.required_headers <= normalized_headers
        ):
            return ProviderResolution(profile.key, profile.mapping, True)
    return ProviderResolution("generic_csv", None, False)
