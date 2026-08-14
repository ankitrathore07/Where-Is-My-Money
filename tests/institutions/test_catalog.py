from app.institutions.catalog import INSTITUTIONS, get_institution, institution_options


def test_institution_catalog_has_stable_known_keys_and_labels() -> None:
    assert tuple((item.key, item.label) for item in INSTITUTIONS) == (
        ("chase", "Chase"),
        ("bank_of_america", "Bank of America"),
        ("citi", "Citi"),
        ("capital_one", "Capital One"),
        ("american_express", "American Express"),
        ("discover", "Discover"),
        ("wells_fargo", "Wells Fargo"),
        ("other", "Other / manual mapping"),
    )
    assert institution_options() == INSTITUTIONS


def test_institution_lookup_rejects_unknown_and_blank_keys() -> None:
    assert get_institution("chase") == INSTITUTIONS[0]
    assert get_institution("missing") is None
    assert get_institution("") is None

