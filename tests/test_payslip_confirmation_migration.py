from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect


def _config(database_url: str) -> Config:
    configured = Config("alembic.ini")
    configured.set_main_option("sqlalchemy.url", database_url)
    return configured


def _income_indexes(database_url: str) -> dict[str, dict[str, object]]:
    engine = create_engine(database_url)
    try:
        return {index["name"]: index for index in inspect(engine).get_indexes("income_records")}
    finally:
        engine.dispose()


def test_payslip_confirmation_unique_index_migrates_and_round_trips(tmp_path: Path) -> None:
    database_url = f"sqlite:///{(tmp_path / 'payslip-confirmation.db').as_posix()}"
    configured = _config(database_url)

    command.upgrade(configured, "head")
    indexes = _income_indexes(database_url)
    assert indexes["uix_income_records_payslip_id"]["unique"] == 1
    assert "ix_income_records_payslip_id" not in indexes

    command.downgrade(configured, "0007_categorization_constraints")
    indexes = _income_indexes(database_url)
    assert indexes["ix_income_records_payslip_id"]["unique"] == 0
    assert "uix_income_records_payslip_id" not in indexes

    command.upgrade(configured, "head")
    assert _income_indexes(database_url)["uix_income_records_payslip_id"]["unique"] == 1
