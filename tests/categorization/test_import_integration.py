from datetime import UTC, datetime
from io import BytesIO
from pathlib import Path

import pytest
from sqlalchemy import event, select
from sqlalchemy.orm import Session

from app.categorization.ai_graph import build_categorization_graph
from app.categorization.ai_types import ClassifierResult
from app.categorization.builtins import BUILTIN_CATEGORY_DEFINITIONS
from app.db.models import Account, Category, MerchantRule, Tag, Transaction, Workspace
from app.imports.service import (
    ReviewValidationError,
    build_review,
    commit_import,
    create_csv_import,
    create_transaction_import,
    save_mapping,
)
from app.imports.storage import LocalUploadStore
from app.imports.types import RowEdit
from app.tags.catalog import BUILTIN_TAG_NAMES

CSV = b"Date,Description,Amount\n08/01/2026,Netflix.com,-15.99\n"
CHASE_HEADER = b"Details,Posting Date,Description,Amount,Type,Balance,Check or Slip #\n"
CHASE_COMPACT_HEADER = b"Date,Description,Amount\n"
PDF_BYTES = b"%PDF-synthetic-provider-parity"


class RecordingClassifier:
    def __init__(self, result: ClassifierResult | None) -> None:
        self.result = result
        self.calls: list[tuple[str, tuple[str, ...]]] = []

    def classify(
        self, description: str, allowed_categories: tuple[str, ...]
    ) -> ClassifierResult | None:
        self.calls.append((description, allowed_categories))
        return self.result


class PdfTextExtractor:
    def __init__(self, text: str) -> None:
        self.text = text

    def extract(self, data: bytes, suffix: str):
        assert data == PDF_BYTES
        assert suffix == ".pdf"
        return type("Extracted", (), {"text": self.text})()


def _seed_builtins(session: Session) -> dict[str, Category]:
    categories = {
        name: Category(
            workspace_id=None,
            name=name,
            name_key=" ".join(name.split()).casefold(),
            kind=kind,
        )
        for name, kind in BUILTIN_CATEGORY_DEFINITIONS
    }
    session.add_all(categories.values())
    session.add_all(Tag(workspace_id=None, name=name) for name in BUILTIN_TAG_NAMES)
    session.commit()
    return categories


def _mapped_job(session: Session, workspace: Workspace, store: LocalUploadStore):
    result = create_csv_import(session, store, workspace, BytesIO(CSV), "retain")
    save_mapping(
        session,
        store,
        result.job,
        {
            "date_column": "Date",
            "description_column": "Description",
            "amount_mode": "single",
            "amount_column": "Amount",
            "date_format": "mdy",
            "amount_sign": "as_is",
        },
    )
    return result.job


def _chase_job(
    session: Session,
    workspace: Workspace,
    store: LocalUploadStore,
    rows: bytes,
):
    account = Account(
        workspace_id=workspace.id,
        name="Chase Checking",
        account_type="checking",
        institution_key="chase",
        institution="Chase",
        is_liability=False,
    )
    session.add(account)
    session.flush()
    return create_csv_import(
        session,
        store,
        workspace,
        BytesIO(CHASE_HEADER + rows),
        "retain",
        account=account,
    ).job


def _chase_compact_job(
    session: Session,
    workspace: Workspace,
    store: LocalUploadStore,
    rows: bytes,
):
    account = Account(
        workspace_id=workspace.id,
        name="Chase Checking",
        account_type="checking",
        institution_key="chase",
        institution="Chase",
        is_liability=False,
    )
    session.add(account)
    session.flush()
    return create_csv_import(
        session,
        store,
        workspace,
        BytesIO(CHASE_COMPACT_HEADER + rows),
        "retain",
        account=account,
    ).job


def test_compact_chase_export_is_mapped_and_categorized_without_manual_setup(
    session: Session, workspace: Workspace, tmp_path: Path
) -> None:
    _seed_builtins(session)
    store = LocalUploadStore(tmp_path)
    job = _chase_compact_job(
        session,
        workspace,
        store,
        b"08/03/2026,MICROSOFT EDIPAYMENT PPD ID: 9911144442,3101.11\n"
        b"08/02/2026,XOOM DEBIT OID 30178544 WEB ID: 1770510487,-500.00\n"
        b"08/01/2026,REMOTE ONLINE DEPOSIT # 1,460.54\n"
        b"07/31/2026,Zelle payment to Sample Payee 30148050922,-100.00\n",
    )

    review = build_review(session, store, job)

    assert job.column_mapping is not None
    assert job.column_mapping["date_column"] == "Date"
    assert [(row.category_name, row.categorization_source) for row in review.rows] == [
        ("Income", "provider_rule"),
        ("Gifts & Donations", "provider_rule"),
        ("Income", "provider_rule"),
        ("Uncategorized", "uncategorized"),
    ]
    family_support_id = session.scalar(select(Tag.id).where(Tag.name_key == "family support"))
    assert review.rows[1].tag_ids == (family_support_id,)


def test_confirmed_chase_rules_carry_tags_and_cadence_into_review(
    session: Session, workspace: Workspace, tmp_path: Path
) -> None:
    _seed_builtins(session)
    store = LocalUploadStore(tmp_path)
    job = _chase_compact_job(
        session,
        workspace,
        store,
        b"08/02/2026,Remitly United S PAYMENTS 440753768551227 CCD ID: 2452441988,-250.00\n"
        b"08/01/2026,Klarna*Interview Kic Columbus OH 03/15,-500.00\n",
    )

    remitly, klarna = build_review(session, store, job).rows

    assert remitly.normalized_merchant == "Remitly"
    assert remitly.category_name == "Gifts & Donations"
    assert [session.get(Tag, tag_id).name for tag_id in remitly.tag_ids] == ["Family Support"]
    assert klarna.normalized_merchant == "Interview Kickstart"
    assert klarna.category_name == "Education"
    assert [session.get(Tag, tag_id).name for tag_id in klarna.tag_ids] == ["Installment Plan"]
    assert klarna.billing_period_months == 1


def test_chase_pdf_and_csv_apply_the_same_provider_categorization(
    session: Session, workspace: Workspace, tmp_path: Path
) -> None:
    _seed_builtins(session)
    account = Account(
        workspace_id=workspace.id,
        name="Chase Checking",
        account_type="checking",
        institution_key="chase",
        institution="Chase",
        is_liability=False,
    )
    session.add(account)
    session.flush()
    store = LocalUploadStore(tmp_path)
    description = "Remitly United S PAYMENTS 440753768551227"
    csv_job = create_csv_import(
        session,
        store,
        workspace,
        BytesIO(CHASE_COMPACT_HEADER + f"01/15/2026,{description},-250.00\n".encode()),
        "retain",
        account=account,
    ).job
    pdf_job = create_transaction_import(
        session,
        store,
        PdfTextExtractor(
            "JPMorgan Chase Bank, N.A.\n"
            "Chase Checking Account Statement\n"
            "January 1, 2026 through January 31, 2026\n"
            f"01/15 {description} -$250.00\n"
        ),
        workspace,
        "checking.pdf",
        "application/pdf",
        BytesIO(PDF_BYTES),
        "retain",
        account=account,
    ).job

    csv_row = build_review(session, store, csv_job).rows[0]
    pdf_row = build_review(
        session,
        store,
        pdf_job,
        PdfTextExtractor(
            "JPMorgan Chase Bank, N.A.\n"
            "Chase Checking Account Statement\n"
            "January 1, 2026 through January 31, 2026\n"
            f"01/15 {description} -$250.00\n"
        ),
    ).rows[0]

    assert (
        pdf_row.normalized_merchant,
        pdf_row.category_name,
        pdf_row.categorization_source,
        pdf_row.tag_ids,
    ) == (
        csv_row.normalized_merchant,
        csv_row.category_name,
        csv_row.categorization_source,
        csv_row.tag_ids,
    )


def test_chase_account_with_unsigned_pdf_header_uses_generic_categorization(
    session: Session, workspace: Workspace, tmp_path: Path
) -> None:
    _seed_builtins(session)
    account = Account(
        workspace_id=workspace.id,
        name="Chase Checking",
        account_type="checking",
        institution_key="chase",
        institution="Chase",
        is_liability=False,
    )
    session.add(account)
    session.flush()
    store = LocalUploadStore(tmp_path)
    extractor = PdfTextExtractor(
        "Checking Account Statement\n"
        "2026-01-15 Remitly United S PAYMENTS 440753768551227 -$250.00\n"
    )
    job = create_transaction_import(
        session,
        store,
        extractor,
        workspace,
        "checking.pdf",
        "application/pdf",
        BytesIO(PDF_BYTES),
        "retain",
        account=account,
    ).job

    row = build_review(session, store, job, extractor).rows[0]

    assert row.category_name == "Uncategorized"
    assert row.categorization_source == "uncategorized"


def test_chase_provider_rule_is_visible_without_ai(
    session: Session, workspace: Workspace, tmp_path: Path
) -> None:
    categories = _seed_builtins(session)
    store = LocalUploadStore(tmp_path)
    job = _chase_job(
        session,
        workspace,
        store,
        b"DEBIT,08/12/2026,BEST BUY AUTO PYMT 240812 123456789,-29.99,ACH,-29.99,\n",
    )

    row = build_review(session, store, job).rows[0]

    assert row.category_id == categories["Transfers"].id
    assert row.category_name == "Transfers"
    assert row.normalized_merchant == "Best Buy Card Payment"
    assert row.is_subscription is False
    assert row.categorization_source == "provider_rule"


def test_review_calls_ai_once_for_repeated_sanitized_description(
    session: Session, workspace: Workspace, tmp_path: Path
) -> None:
    categories = _seed_builtins(session)
    store = LocalUploadStore(tmp_path)
    job = _chase_job(
        session,
        workspace,
        store,
        b"CREDIT,08/11/2026,MICROSOFT EDIPAYMENT 111111111,-10.00,ACH,90.00,\n"
        b"CREDIT,08/12/2026,MICROSOFT EDIPAYMENT 222222222,-20.00,ACH,70.00,\n",
    )
    classifier = RecordingClassifier(ClassifierResult("Shopping", False, False))

    review = build_review(
        session,
        store,
        job,
        categorization_graph=build_categorization_graph(classifier),
    )

    assert [row.category_id for row in review.rows] == [
        categories["Shopping"].id,
        categories["Shopping"].id,
    ]
    assert [row.categorization_source for row in review.rows] == [
        "ai_suggestion",
        "ai_suggestion",
    ]
    assert classifier.calls == [
        (
            "MICROSOFT EDIPAYMENT",
            tuple(
                sorted(
                    name for name, _kind in BUILTIN_CATEGORY_DEFINITIONS if name != "Uncategorized"
                )
            ),
        )
    ]


def test_provider_rule_never_calls_ai(
    session: Session, workspace: Workspace, tmp_path: Path
) -> None:
    _seed_builtins(session)
    store = LocalUploadStore(tmp_path)
    job = _chase_job(
        session,
        workspace,
        store,
        b"DEBIT,08/12/2026,BEST BUY AUTO PYMT 123456789,-29.99,ACH,-29.99,\n",
    )

    class FailIfCalled:
        def classify(
            self, description: str, allowed_categories: tuple[str, ...]
        ) -> ClassifierResult | None:
            raise AssertionError("deterministic rows must skip AI")

    row = build_review(
        session,
        store,
        job,
        categorization_graph=build_categorization_graph(FailIfCalled()),
    ).rows[0]

    assert row.categorization_source == "provider_rule"


def test_ai_category_with_wrong_local_direction_is_discarded(
    session: Session, workspace: Workspace, tmp_path: Path
) -> None:
    categories = _seed_builtins(session)
    store = LocalUploadStore(tmp_path)
    job = _chase_job(
        session,
        workspace,
        store,
        b"DEBIT,08/12/2026,UNKNOWN OUTGOING 123456789,-29.99,ACH,-29.99,\n",
    )
    classifier = RecordingClassifier(ClassifierResult("Income", False, False))

    row = build_review(
        session,
        store,
        job,
        categorization_graph=build_categorization_graph(classifier),
    ).rows[0]

    assert row.category_id == categories["Uncategorized"].id
    assert row.categorization_source == "uncategorized"


def test_accepted_ai_suggestion_commits_without_creating_a_rule(
    session: Session, workspace: Workspace, tmp_path: Path
) -> None:
    categories = _seed_builtins(session)
    store = LocalUploadStore(tmp_path)
    job = _chase_job(
        session,
        workspace,
        store,
        b"DEBIT,08/12/2026,UNKNOWN SHOP 123456789,-29.99,ACH,-29.99,\n",
    )
    graph = build_categorization_graph(
        RecordingClassifier(ClassifierResult("Shopping", True, False))
    )
    row = build_review(session, store, job, categorization_graph=graph).rows[0]

    commit_import(
        session,
        store,
        job,
        (
            RowEdit(
                row.row_number,
                True,
                row.date_value,
                row.description_value,
                row.amount_value,
                normalized_merchant=row.normalized_merchant,
                category_id=row.category_id,
                is_subscription=row.is_subscription,
                categorization_source=row.categorization_source,
                original_normalized_merchant=row.normalized_merchant,
                original_category_id=row.category_id,
                original_is_subscription=row.is_subscription,
                original_categorization_source=row.categorization_source,
            ),
        ),
    )
    transaction = session.scalar(select(Transaction))

    assert transaction is not None
    assert transaction.category_id == categories["Shopping"].id
    assert transaction.is_subscription is True
    assert transaction.categorization_source == "ai_suggestion"
    assert session.scalar(select(MerchantRule)) is None


def test_builtin_decision_is_visible_in_import_preview(
    session: Session, workspace: Workspace, tmp_path: Path
) -> None:
    categories = _seed_builtins(session)
    store = LocalUploadStore(tmp_path)
    job = _mapped_job(session, workspace, store)

    row = build_review(session, store, job).rows[0]

    assert row.normalized_merchant == "Netflix"
    assert row.category_id == categories["Entertainment"].id
    assert row.category_name == "Entertainment"
    assert row.is_subscription is True
    assert row.categorization_source == "builtin_rule"


def test_import_review_infers_recurring_cadence_from_transaction_history(
    session: Session, workspace: Workspace, tmp_path: Path
) -> None:
    categories = _seed_builtins(session)
    session.add_all(
        [
            Transaction(
                workspace_id=workspace.id,
                date=datetime(2026, 1, 15, tzinfo=UTC),
                description="STATE FARM",
                normalized_merchant="State Farm",
                amount_cents=-12_000,
                category_id=categories["Insurance"].id,
                categorization_source="builtin_rule",
                is_subscription=False,
            ),
            Transaction(
                workspace_id=workspace.id,
                date=datetime(2026, 4, 15, tzinfo=UTC),
                description="STATE FARM",
                normalized_merchant="State Farm",
                amount_cents=-12_000,
                category_id=categories["Insurance"].id,
                categorization_source="builtin_rule",
                is_subscription=False,
            ),
        ]
    )
    session.commit()
    store = LocalUploadStore(tmp_path)
    result = create_csv_import(
        session,
        store,
        workspace,
        BytesIO(b"Date,Description,Amount\n07/15/2026,STATE FARM,-120.00\n"),
        "retain",
    )
    save_mapping(
        session,
        store,
        result.job,
        {
            "date_column": "Date",
            "description_column": "Description",
            "amount_mode": "single",
            "amount_column": "Amount",
            "date_format": "mdy",
            "amount_sign": "as_is",
        },
    )

    row = build_review(session, store, result.job).rows[0]

    assert row.billing_period_months == 3
    assert row.is_subscription is False


def test_workspace_rule_overrides_builtin_without_cross_workspace_leakage(
    session: Session,
    workspace: Workspace,
    other_workspace: Workspace,
    tmp_path: Path,
) -> None:
    _seed_builtins(session)
    owned = Category(
        workspace_id=workspace.id,
        name="Household Media",
        name_key="household media",
        kind="expense",
    )
    foreign = Category(
        workspace_id=other_workspace.id,
        name="Foreign Media",
        name_key="foreign media",
        kind="expense",
    )
    session.add_all([owned, foreign])
    session.flush()
    session.add_all(
        [
            MerchantRule(
                workspace_id=workspace.id,
                merchant_pattern="NETFLIX COM",
                normalized_merchant="Home Streaming",
                category=owned,
                is_subscription=False,
            ),
            MerchantRule(
                workspace_id=other_workspace.id,
                merchant_pattern="NETFLIX COM",
                normalized_merchant="Foreign Streaming",
                category=foreign,
                is_subscription=True,
            ),
        ]
    )
    session.commit()
    store = LocalUploadStore(tmp_path)

    row = build_review(session, store, _mapped_job(session, workspace, store)).rows[0]

    assert row.normalized_merchant == "Home Streaming"
    assert row.category_id == owned.id
    assert row.is_subscription is False
    assert row.categorization_source == "workspace_rule"


def test_import_compiles_typed_workspace_rules_once_and_supplies_account_context(
    session: Session, workspace: Workspace, tmp_path: Path
) -> None:
    """Break if an import reloads rules per row or omits its authorized account context."""
    _seed_builtins(session)
    category = Category(
        workspace_id=workspace.id,
        name="Account streaming",
        name_key="account streaming",
        kind="expense",
    )
    account = Account(
        workspace_id=workspace.id,
        name="Streaming card",
        account_type="credit_card",
        institution_key="other",
        institution="Local",
        is_liability=True,
    )
    session.add_all([category, account])
    session.flush()
    session.add(
        MerchantRule(
            workspace_id=workspace.id,
            name="Account-specific streaming",
            priority=0,
            condition_json={
                "version": 1,
                "type": "all",
                "children": [
                    {
                        "type": "predicate",
                        "field": "description",
                        "operator": "contains",
                        "value": "STREAMING TEST",
                    },
                    {
                        "type": "predicate",
                        "field": "account_id",
                        "operator": "equal",
                        "value": account.id,
                    },
                ],
            },
            normalized_merchant="Streaming test",
            category=category,
        )
    )
    session.commit()
    store = LocalUploadStore(tmp_path)
    rows = b"".join(
        f"08/0{index}/2026,STREAMING TEST {index},-15.99\n".encode() for index in range(1, 6)
    )
    job = create_csv_import(
        session,
        store,
        workspace,
        BytesIO(b"Date,Description,Amount\n" + rows),
        "retain",
        account=account,
    ).job
    save_mapping(
        session,
        store,
        job,
        {
            "date_column": "Date",
            "description_column": "Description",
            "amount_mode": "single",
            "amount_column": "Amount",
            "date_format": "mdy",
            "amount_sign": "as_is",
        },
    )
    assert session.bind is not None
    rule_queries: list[str] = []

    def capture_rule_query(
        _connection: object,
        _cursor: object,
        statement: str,
        _parameters: object,
        _context: object,
        _executemany: object,
    ) -> None:
        if "merchant_rules" in statement.casefold():
            rule_queries.append(statement)

    event.listen(session.bind, "before_cursor_execute", capture_rule_query)
    try:
        review = build_review(session, store, job)
    finally:
        event.remove(session.bind, "before_cursor_execute", capture_rule_query)

    assert [row.categorization_source for row in review.rows] == ["workspace_rule"] * 5
    assert [row.category_id for row in review.rows] == [category.id] * 5
    assert len(rule_queries) <= 2


def test_commit_corrected_invalid_rows_reuses_account_and_provider_rule_context(
    session: Session, workspace: Workspace, tmp_path: Path
) -> None:
    """Break if corrected rows drop import context or compile workspace rules per row."""
    _seed_builtins(session)
    store = LocalUploadStore(tmp_path)
    job = _chase_job(
        session,
        workspace,
        store,
        b"DEBIT,NOT-A-DATE,CORRECTED ACCOUNT MERCHANT ONE,-10.00,ACH,-10.00,\n"
        b"DEBIT,STILL-NOT-A-DATE,CORRECTED ACCOUNT MERCHANT TWO,-20.00,ACH,-30.00,\n",
    )
    category = Category(
        workspace_id=workspace.id,
        name="Corrected account rule",
        name_key="corrected account rule",
        kind="expense",
    )
    session.add(category)
    session.flush()
    assert job.account_id is not None
    session.add(
        MerchantRule(
            workspace_id=workspace.id,
            name="Corrected Chase account",
            priority=0,
            condition_json={
                "version": 1,
                "type": "all",
                "children": [
                    {
                        "type": "predicate",
                        "field": "description",
                        "operator": "contains",
                        "value": "CORRECTED ACCOUNT MERCHANT",
                    },
                    {
                        "type": "predicate",
                        "field": "account_id",
                        "operator": "equal",
                        "value": job.account_id,
                    },
                    {
                        "type": "predicate",
                        "field": "provider_key",
                        "operator": "equal",
                        "value": "chase_bank_csv",
                    },
                ],
            },
            normalized_merchant="Corrected account merchant",
            category=category,
        )
    )
    session.commit()
    assert session.bind is not None
    rule_loads: list[str] = []

    def capture_rule_load(
        _connection: object,
        _cursor: object,
        statement: str,
        _parameters: object,
        _context: object,
        _executemany: object,
    ) -> None:
        normalized = " ".join(statement.casefold().split())
        if "from merchant_rules" in normalized and "merchant_rule_tags" not in normalized:
            rule_loads.append(normalized)

    event.listen(session.bind, "before_cursor_execute", capture_rule_load)
    try:
        commit_import(
            session,
            store,
            job,
            (
                RowEdit(
                    2,
                    True,
                    "2026-08-01",
                    "CORRECTED ACCOUNT MERCHANT ONE",
                    "-10.00",
                ),
                RowEdit(
                    3,
                    True,
                    "2026-08-02",
                    "CORRECTED ACCOUNT MERCHANT TWO",
                    "-20.00",
                ),
            ),
        )
    finally:
        event.remove(session.bind, "before_cursor_execute", capture_rule_load)
    transactions = tuple(session.scalars(select(Transaction).order_by(Transaction.id)))

    assert [transaction.category_id for transaction in transactions] == [category.id, category.id]
    assert [transaction.normalized_merchant for transaction in transactions] == [
        "Corrected account merchant",
        "Corrected account merchant",
    ]
    assert [transaction.categorization_source for transaction in transactions] == [
        "manual",
        "manual",
    ]
    assert len(rule_loads) == 1


def test_commit_preserves_preview_decision_even_if_rule_changes(
    session: Session, workspace: Workspace, tmp_path: Path
) -> None:
    categories = _seed_builtins(session)
    store = LocalUploadStore(tmp_path)
    job = _mapped_job(session, workspace, store)
    row = build_review(session, store, job).rows[0]
    session.add(
        MerchantRule(
            workspace_id=workspace.id,
            merchant_pattern="NETFLIX COM",
            normalized_merchant="Changed After Preview",
            category_id=categories["Shopping"].id,
            is_subscription=False,
        )
    )
    session.commit()

    commit_import(
        session,
        store,
        job,
        (
            RowEdit(
                row.row_number,
                True,
                row.date_value,
                row.description_value,
                row.amount_value,
                normalized_merchant=row.normalized_merchant,
                category_id=row.category_id,
                is_subscription=row.is_subscription,
                categorization_source=row.categorization_source,
                original_normalized_merchant=row.normalized_merchant,
                original_category_id=row.category_id,
                original_is_subscription=row.is_subscription,
                original_categorization_source=row.categorization_source,
            ),
        ),
    )
    transaction = session.scalar(select(Transaction))

    assert transaction is not None
    assert transaction.normalized_merchant == "Netflix"
    assert transaction.category_id == categories["Entertainment"].id
    assert transaction.is_subscription is True
    assert transaction.categorization_source == "builtin_rule"


def test_review_override_commits_as_manual_without_creating_rule(
    session: Session, workspace: Workspace, tmp_path: Path
) -> None:
    categories = _seed_builtins(session)
    store = LocalUploadStore(tmp_path)
    job = _mapped_job(session, workspace, store)
    row = build_review(session, store, job).rows[0]

    commit_import(
        session,
        store,
        job,
        (
            RowEdit(
                row.row_number,
                True,
                row.date_value,
                row.description_value,
                row.amount_value,
                normalized_merchant="Movie Night",
                category_id=categories["Shopping"].id,
                is_subscription=False,
                categorization_source=row.categorization_source,
                original_normalized_merchant=row.normalized_merchant,
                original_category_id=row.category_id,
                original_is_subscription=row.is_subscription,
                original_categorization_source=row.categorization_source,
            ),
        ),
    )
    transaction = session.scalar(select(Transaction))

    assert transaction is not None
    assert transaction.description == "Netflix.com"
    assert transaction.normalized_merchant == "Movie Night"
    assert transaction.category_id == categories["Shopping"].id
    assert transaction.is_subscription is False
    assert transaction.categorization_source == "manual"
    assert session.scalar(select(MerchantRule)) is None


def test_manual_review_override_beats_matching_typed_workspace_rule(
    session: Session, workspace: Workspace, tmp_path: Path
) -> None:
    """Break if a typed workspace match can retain authority over a manual review edit."""
    categories = _seed_builtins(session)
    workspace_category = Category(
        workspace_id=workspace.id,
        name="Typed streaming",
        name_key="typed streaming",
        kind="expense",
    )
    session.add(workspace_category)
    session.flush()
    rule = MerchantRule(
        workspace_id=workspace.id,
        name="Typed Netflix",
        priority=0,
        condition_json={
            "version": 1,
            "type": "predicate",
            "field": "merchant_key",
            "operator": "exact",
            "value": "NETFLIX COM",
        },
        normalized_merchant="Typed Netflix",
        category=workspace_category,
    )
    session.add(rule)
    session.commit()
    store = LocalUploadStore(tmp_path)
    job = _mapped_job(session, workspace, store)
    row = build_review(session, store, job).rows[0]
    assert row.categorization_source == "workspace_rule"

    commit_import(
        session,
        store,
        job,
        (
            RowEdit(
                row.row_number,
                True,
                row.date_value,
                row.description_value,
                row.amount_value,
                normalized_merchant="Manual movie night",
                category_id=categories["Shopping"].id,
                is_subscription=False,
                categorization_source=row.categorization_source,
                original_normalized_merchant=row.normalized_merchant,
                original_category_id=row.category_id,
                original_is_subscription=row.is_subscription,
                original_categorization_source=row.categorization_source,
            ),
        ),
    )
    transaction = session.scalar(select(Transaction))

    assert transaction is not None
    assert transaction.normalized_merchant == "Manual movie night"
    assert transaction.category_id == categories["Shopping"].id
    assert transaction.categorization_source == "manual"
    assert session.get(MerchantRule, rule.id) is not None


def test_review_commit_persists_multiple_tags_and_billing_cadence(
    session: Session, workspace: Workspace, tmp_path: Path
) -> None:
    _seed_builtins(session)
    store = LocalUploadStore(tmp_path)
    job = _mapped_job(session, workspace, store)
    row = build_review(session, store, job).rows[0]
    household_id = session.scalar(select(Tag.id).where(Tag.name_key == "household expenditure"))
    vehicle_id = session.scalar(select(Tag.id).where(Tag.name_key == "vehicle"))
    assert household_id is not None and vehicle_id is not None

    commit_import(
        session,
        store,
        job,
        (
            RowEdit(
                row.row_number,
                True,
                row.date_value,
                row.description_value,
                row.amount_value,
                normalized_merchant=row.normalized_merchant,
                category_id=row.category_id,
                is_subscription=True,
                categorization_source=row.categorization_source,
                tag_ids=(household_id, vehicle_id),
                billing_period_months=12,
                original_normalized_merchant=row.normalized_merchant,
                original_category_id=row.category_id,
                original_is_subscription=row.is_subscription,
                original_categorization_source=row.categorization_source,
                original_tag_ids=row.tag_ids,
                original_billing_period_months=row.billing_period_months,
            ),
        ),
    )
    transaction = session.scalar(select(Transaction))

    assert transaction is not None
    assert [tag.name for tag in transaction.tags] == [
        "Household Expenditure",
        "Subscription",
        "Vehicle",
    ]
    assert transaction.billing_period_months == 12


def test_import_commit_rejects_tag_from_another_workspace(
    session: Session,
    workspace: Workspace,
    other_workspace: Workspace,
    tmp_path: Path,
) -> None:
    _seed_builtins(session)
    foreign = Tag(workspace_id=other_workspace.id, name="Foreign Tag")
    session.add(foreign)
    session.commit()
    store = LocalUploadStore(tmp_path)
    job = _mapped_job(session, workspace, store)
    row = build_review(session, store, job).rows[0]

    with pytest.raises(ReviewValidationError) as error:
        commit_import(
            session,
            store,
            job,
            (
                RowEdit(
                    row.row_number,
                    True,
                    row.date_value,
                    row.description_value,
                    row.amount_value,
                    category_id=row.category_id,
                    is_subscription=row.is_subscription,
                    tag_ids=(foreign.id,),
                ),
            ),
        )

    assert error.value.row_errors[row.row_number] == {
        "tags": "Choose a valid categorization value."
    }


@pytest.mark.parametrize("billing_period_months", [0, 121])
def test_import_commit_rejects_invalid_billing_cadence(
    session: Session,
    workspace: Workspace,
    tmp_path: Path,
    billing_period_months: int,
) -> None:
    _seed_builtins(session)
    store = LocalUploadStore(tmp_path)
    job = _mapped_job(session, workspace, store)
    row = build_review(session, store, job).rows[0]

    with pytest.raises(ReviewValidationError) as error:
        commit_import(
            session,
            store,
            job,
            (
                RowEdit(
                    row.row_number,
                    True,
                    row.date_value,
                    row.description_value,
                    row.amount_value,
                    category_id=row.category_id,
                    is_subscription=row.is_subscription,
                    billing_period_months=billing_period_months,
                    billing_period_submitted=True,
                ),
            ),
        )

    assert error.value.row_errors[row.row_number] == {
        "billing_period_months": "Choose a valid categorization value."
    }


def test_import_commit_rejects_category_from_another_workspace(
    session: Session,
    workspace: Workspace,
    other_workspace: Workspace,
    tmp_path: Path,
) -> None:
    _seed_builtins(session)
    foreign_category = Category(
        workspace_id=other_workspace.id,
        name="Foreign Only",
        name_key="foreign only",
        kind="expense",
    )
    session.add(foreign_category)
    session.commit()
    store = LocalUploadStore(tmp_path)
    job = _mapped_job(session, workspace, store)
    row = build_review(session, store, job).rows[0]

    with pytest.raises(ReviewValidationError) as error:
        commit_import(
            session,
            store,
            job,
            (
                RowEdit(
                    row.row_number,
                    True,
                    row.date_value,
                    row.description_value,
                    row.amount_value,
                    normalized_merchant=row.normalized_merchant,
                    category_id=foreign_category.id,
                    is_subscription=row.is_subscription,
                    categorization_source=row.categorization_source,
                    original_normalized_merchant=row.normalized_merchant,
                    original_category_id=row.category_id,
                    original_is_subscription=row.is_subscription,
                    original_categorization_source=row.categorization_source,
                ),
            ),
        )

    assert error.value.row_errors[row.row_number] == {
        "category": "Choose a valid categorization value."
    }
    assert session.scalar(select(Transaction)) is None
    assert session.scalar(select(MerchantRule)) is None
