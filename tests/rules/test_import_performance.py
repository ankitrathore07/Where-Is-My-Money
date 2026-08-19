from contextlib import contextmanager
from io import BytesIO
from types import SimpleNamespace

from sqlalchemy import event
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

import app.imports.service as import_service
from app.db.models import Category, MerchantRule, Workspace
from app.imports.service import build_review, create_csv_import, save_mapping
from app.imports.storage import LocalUploadStore


@contextmanager
def _merchant_rule_query_counter(engine: Engine):
    count = SimpleNamespace(value=0)

    def increment(
        _connection: object,
        _cursor: object,
        statement: str,
        _parameters: object,
        _context: object,
        _executemany: object,
    ) -> None:
        normalized = " ".join(statement.casefold().split())
        if " from merchant_rules " in f" {normalized} ":
            count.value += 1

    event.listen(engine, "before_cursor_execute", increment)
    try:
        yield count
    finally:
        event.remove(engine, "before_cursor_execute", increment)


def test_import_rule_compilation_and_queries_are_constant_at_1000_rows(
    session: Session,
    workspace: Workspace,
    tmp_path,
    monkeypatch,
) -> None:
    """Break if review compilation or merchant-rule loading moves inside the row loop."""
    category = Category(
        workspace_id=workspace.id,
        name="Streaming",
        name_key="streaming",
        kind="expense",
    )
    rule = MerchantRule(
        workspace_id=workspace.id,
        name="Streaming",
        priority=0,
        condition_json={
            "version": 1,
            "type": "predicate",
            "field": "description",
            "operator": "contains",
            "value": "STREAMING TEST",
        },
        normalized_merchant="Streaming",
        category=category,
    )
    session.add(rule)
    session.commit()
    rows = b"".join(
        f"08/01/2026,STREAMING TEST {index},-{index + 1}.00\n".encode() for index in range(1000)
    )
    store = LocalUploadStore(tmp_path)
    job = create_csv_import(
        session,
        store,
        workspace,
        BytesIO(b"Date,Description,Amount\n" + rows),
        "retain",
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
    real_load = import_service.load_compiled_rule_set
    compilation_count = SimpleNamespace(value=0)

    def counted_load(*args, **kwargs):
        compilation_count.value += 1
        return real_load(*args, **kwargs)

    monkeypatch.setattr(import_service, "load_compiled_rule_set", counted_load)
    assert session.bind is not None

    with _merchant_rule_query_counter(session.bind) as query_count:
        review = build_review(session, store, job)

    assert len(review.rows) == 1000
    assert {row.merchant_rule_id for row in review.rows} == {rule.id}
    assert compilation_count.value == 1
    assert query_count.value == 1
