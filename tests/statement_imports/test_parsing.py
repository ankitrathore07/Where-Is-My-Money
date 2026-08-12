from datetime import date

import pytest

from app.statement_imports.parsing import StatementFormatError, parse_wimm_csv


def test_wimm_csv_parses_exact_one_row_contract() -> None:
    data = (
        b"\xef\xbb\xbfaccount_name,institution,account_last_four,total_balance,as_of_date\n"
        b"Northstar Brokerage,Fictional Brokerage,4821,$125430.18,2026-07-31\n"
    )

    candidate = parse_wimm_csv(data)

    assert candidate.account_name == "Northstar Brokerage"
    assert candidate.institution == "Fictional Brokerage"
    assert candidate.account_last_four == "4821"
    assert candidate.balance_cents == 12_543_018
    assert candidate.as_of_date == date(2026, 7, 31)
    assert candidate.extraction_method == "wimm_csv"


@pytest.mark.parametrize(
    ("data", "code"),
    [
        (b"name,balance,date\nA,1.00,2026-07-31\n", "invalid_csv_header"),
        (
            b"account_name,institution,account_last_four,total_balance,as_of_date,extra\n"
            b"A,,,1.00,2026-07-31,x\n",
            "invalid_csv_header",
        ),
        (
            b"account_name,institution,account_last_four,total_balance,as_of_date\n"
            b"A,,,1.00,2026-07-31\nB,,,2.00,2026-07-31\n",
            "invalid_csv_rows",
        ),
        (
            b"account_name,institution,account_last_four,total_balance,as_of_date\n"
            b",,,1.00,2026-07-31\n",
            "missing_account_identity",
        ),
        (
            b"account_name,institution,account_last_four,total_balance,as_of_date\n"
            b"A,,12A4,1.00,2026-07-31\n",
            "invalid_account_last_four",
        ),
        (
            b"account_name,institution,account_last_four,total_balance,as_of_date\n"
            b"A,,,-1.00,2026-07-31\n",
            "invalid_balance",
        ),
        (
            b"account_name,institution,account_last_four,total_balance,as_of_date\n"
            b"A,,,1.001,2026-07-31\n",
            "invalid_balance",
        ),
        (
            b"account_name,institution,account_last_four,total_balance,as_of_date\n"
            b"A,,,=SUM(1),2026-07-31\n",
            "invalid_csv_formula",
        ),
        (
            b"account_name,institution,account_last_four,total_balance,as_of_date\n"
            b"A,,,1.00,07/31/2026\n",
            "invalid_date",
        ),
        (
            b"account_name,institution,account_last_four,total_balance,as_of_date\n"
            b"=CMD(),,,1.00,2026-07-31\n",
            "invalid_csv_formula",
        ),
        (
            b"account_name,institution,account_last_four,total_balance,as_of_date\n"
            b"A,=CMD(),,1.00,2026-07-31\n",
            "invalid_csv_formula",
        ),
        (
            b"account_name,institution,account_last_four,total_balance,as_of_date\n"
            b"A,,,1.00,20260731\n",
            "invalid_date",
        ),
        (
            b"account_name,institution,account_last_four,total_balance,as_of_date\n"
            b"A,,,1.00,2026-W31-5\n",
            "invalid_date",
        ),
    ],
)
def test_wimm_csv_rejects_content_outside_exact_contract(data: bytes, code: str) -> None:
    with pytest.raises(StatementFormatError) as error:
        parse_wimm_csv(data)
    assert error.value.code == code
