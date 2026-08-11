from pathlib import Path

import pytest

from app.payslips.parsing import ReviewValidationError, extract_candidates, validate_review

FIXTURE = Path(__file__).parents[1] / "fixtures" / "payslips" / "synthetic_paystub_text.txt"


def test_labeled_synthetic_text_produces_exact_candidate_values() -> None:
    candidate = extract_candidates(FIXTURE.read_text(encoding="utf-8"))

    assert candidate.employer == "Northstar Bicycle Works"
    assert candidate.pay_period_start.isoformat() == "2026-07-01"
    assert candidate.pay_period_end.isoformat() == "2026-07-15"
    assert candidate.pay_date.isoformat() == "2026-07-20"
    assert candidate.gross_pay_cents == 500000
    assert candidate.net_pay_cents == 370000
    assert candidate.taxes_cents == 90000
    assert candidate.deductions_cents == 40000
    assert candidate.to_json() == {
        "employer": "Northstar Bicycle Works",
        "pay_period_start": "2026-07-01",
        "pay_period_end": "2026-07-15",
        "pay_date": "2026-07-20",
        "gross_pay_cents": 500000,
        "net_pay_cents": 370000,
        "taxes_cents": 90000,
        "deductions_cents": 40000,
    }


def test_candidate_parser_accepts_common_label_and_money_variants() -> None:
    candidate = extract_candidates(
        """
        Company: Fictional Orchard LLC
        Pay period: 07/01/2026 to 07/15/2026
        Payment date: 07/20/2026
        Gross earnings 5000
        Total taxes ($900.50)
        Total deductions: (400.25)
        Take home pay: 3699.25
        """
    )

    assert candidate.employer == "Fictional Orchard LLC"
    assert candidate.pay_period_start.isoformat() == "2026-07-01"
    assert candidate.pay_period_end.isoformat() == "2026-07-15"
    assert candidate.pay_date.isoformat() == "2026-07-20"
    assert candidate.gross_pay_cents == 500000
    assert candidate.net_pay_cents == 369925
    assert candidate.taxes_cents == 90050
    assert candidate.deductions_cents == 40025


def test_missing_or_malformed_labels_remain_empty_instead_of_guessing() -> None:
    candidate = extract_candidates(
        """
        Employee number: 500000
        Check number: 370000
        Gross Pay: not available
        Deposit total: $3,700.00
        """
    )

    assert candidate.employer is None
    assert candidate.pay_period_start is None
    assert candidate.pay_period_end is None
    assert candidate.pay_date is None
    assert candidate.gross_pay_cents is None
    assert candidate.net_pay_cents is None
    assert candidate.taxes_cents is None
    assert candidate.deductions_cents is None


def test_candidate_labels_are_anchored_to_lines() -> None:
    candidate = extract_candidates(
        "Memo: prior Gross Pay: $9,999.00\nNot Net Pay: $8,888.00\nTaxes withheld elsewhere"
    )

    assert candidate.gross_pay_cents is None
    assert candidate.net_pay_cents is None
    assert candidate.taxes_cents is None


def test_extracted_candidates_respect_database_and_display_bounds() -> None:
    candidate = extract_candidates(
        "\n".join(
            [
                f"Employer: {'x' * 256}",
                "Gross Pay: $21,474,836.48",
                "Net Pay: $21,474,836.47",
            ]
        )
    )

    assert candidate.employer is None
    assert candidate.gross_pay_cents is None
    assert candidate.net_pay_cents == 2_147_483_647


def _valid_review(**overrides: str) -> dict[str, str]:
    values = {
        "employer": "Edited Employer",
        "pay_period_start": "2026-07-02",
        "pay_period_end": "2026-07-16",
        "pay_date": "2026-07-21",
        "gross_pay": "5,100.25",
        "net_pay": "3800.10",
        "taxes": "900.00",
        "deductions": "400.15",
    }
    values.update(overrides)
    return values


def test_review_uses_edited_literal_values() -> None:
    values = validate_review(_valid_review())

    assert values.employer == "Edited Employer"
    assert values.pay_period_start.isoformat() == "2026-07-02"
    assert values.pay_period_end.isoformat() == "2026-07-16"
    assert values.pay_date.isoformat() == "2026-07-21"
    assert values.gross_pay_cents == 510025
    assert values.net_pay_cents == 380010
    assert values.taxes_cents == 90000
    assert values.deductions_cents == 40015


def test_review_allows_blank_optional_employer_and_pay_period() -> None:
    values = validate_review(_valid_review(employer="  ", pay_period_start="", pay_period_end=""))

    assert values.employer is None
    assert values.pay_period_start is None
    assert values.pay_period_end is None


def test_review_collects_field_specific_errors_without_returning_values() -> None:
    with pytest.raises(ReviewValidationError) as error:
        validate_review(
            _valid_review(
                employer="x" * 256,
                pay_period_start="not-a-date",
                pay_period_end="2026-07-01",
                pay_date="",
                gross_pay="-1.00",
                net_pay="12.345",
                taxes="not-money",
                deductions="",
            )
        )

    assert error.value.field_errors == {
        "employer": "Use 255 characters or fewer.",
        "pay_period_start": "Enter a valid date in YYYY-MM-DD format.",
        "pay_date": "Enter a valid pay date in YYYY-MM-DD format.",
        "gross_pay": "Enter a non-negative amount with at most two decimal places.",
        "net_pay": "Enter a non-negative amount with at most two decimal places.",
        "taxes": "Enter a non-negative amount with at most two decimal places.",
        "deductions": "Enter a non-negative amount with at most two decimal places.",
    }


def test_review_rejects_pay_period_end_before_start() -> None:
    with pytest.raises(ReviewValidationError) as error:
        validate_review(_valid_review(pay_period_start="2026-07-16", pay_period_end="2026-07-15"))

    assert error.value.field_errors == {
        "pay_period_end": "Pay-period end cannot be before its start."
    }


def test_review_money_must_fit_the_database_integer_column() -> None:
    values = validate_review(
        _valid_review(
            gross_pay="21,474,836.47",
            net_pay="21,474,836.47",
            taxes="21,474,836.47",
            deductions="21,474,836.47",
        )
    )
    assert values.gross_pay_cents == 2_147_483_647

    with pytest.raises(ReviewValidationError) as error:
        validate_review(_valid_review(gross_pay="21,474,836.48"))

    assert error.value.field_errors == {
        "gross_pay": "Enter an amount no greater than $21,474,836.47."
    }
