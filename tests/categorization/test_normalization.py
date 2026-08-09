import pytest

from app.categorization.normalization import merchant_display_fallback, merchant_key


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("  Netflix.com *  ", "NETFLIX COM"),
        ("WHOLE—FOODS   MARKET", "WHOLE FOODS MARKET"),
        ("Café  Déjà Vu", "CAFÉ DÉJÀ VU"),
        ("Ｆｕｌｌｗｉｄｔｈ １２３", "FULLWIDTH 123"),
        ("***", ""),
    ],
)
def test_merchant_key_normalizes_statement_text(raw: str, expected: str) -> None:
    assert merchant_key(raw) == expected


def test_display_fallback_collapses_surrounding_and_repeated_whitespace() -> None:
    assert merchant_display_fallback("  Corner \t Store\n") == "Corner Store"


def test_display_fallback_limits_database_value_to_255_characters() -> None:
    assert merchant_display_fallback("x" * 300) == "x" * 255
