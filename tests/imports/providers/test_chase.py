from app.imports.providers.chase import CHASE_PROVIDER_PROFILES


def test_chase_profiles_are_scoped_to_their_account_types() -> None:
    by_key = {profile.key: profile for profile in CHASE_PROVIDER_PROFILES}

    assert by_key["chase_bank_csv"].account_types == frozenset({"checking", "savings"})
    assert by_key["chase_credit_card_csv"].account_types == frozenset({"credit_card"})
    assert all(profile.institution_key == "chase" for profile in by_key.values())
    assert all(profile.suffixes == frozenset({".csv"}) for profile in by_key.values())
