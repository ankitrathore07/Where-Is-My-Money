from collections.abc import Mapping
from datetime import date, datetime
from typing import Any

from sqlalchemy import (
    JSON,
    Boolean,
    CheckConstraint,
    Column,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Table,
    UniqueConstraint,
    false,
    func,
    text,
    true,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship, validates

from app.rules.application_tokens import ApplicationSelectionJSON, validate_application_digest


class Base(DeclarativeBase):
    pass


def _default_category_name_key(context: Any) -> str:
    """Derive a stable key for legacy constructors; services still validate explicit input."""
    name = str(context.get_current_parameters()["name"])
    return " ".join(name.split()).casefold()


transaction_tags = Table(
    "transaction_tags",
    Base.metadata,
    Column(
        "transaction_id",
        ForeignKey("transactions.id", ondelete="CASCADE"),
        primary_key=True,
    ),
    Column("tag_id", ForeignKey("tags.id", ondelete="CASCADE"), primary_key=True),
)


merchant_rule_tags = Table(
    "merchant_rule_tags",
    Base.metadata,
    Column(
        "merchant_rule_id",
        ForeignKey("merchant_rules.id", ondelete="CASCADE"),
        primary_key=True,
    ),
    Column("tag_id", ForeignKey("tags.id", ondelete="CASCADE"), primary_key=True),
)


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)
    google_sub: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    email: Mapped[str] = mapped_column(String(320), unique=True, index=True)
    display_name: Mapped[str | None] = mapped_column(String(255))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    owned_workspaces: Mapped[list["Workspace"]] = relationship(back_populates="owner")
    memberships: Mapped[list["WorkspaceMembership"]] = relationship(back_populates="user")
    sent_invitations: Mapped[list["WorkspaceInvitation"]] = relationship(
        back_populates="invited_by"
    )
    initiated_rule_application_runs: Mapped[list["RuleApplicationRun"]] = relationship(
        back_populates="initiated_by_user"
    )


class Workspace(Base):
    __tablename__ = "workspaces"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(255))
    is_personal: Mapped[bool] = mapped_column(Boolean, default=False)
    owner_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    owner: Mapped["User"] = relationship(back_populates="owned_workspaces")
    memberships: Mapped[list["WorkspaceMembership"]] = relationship(back_populates="workspace")
    invitations: Mapped[list["WorkspaceInvitation"]] = relationship(back_populates="workspace")
    uploaded_files: Mapped[list["UploadedFile"]] = relationship(back_populates="workspace")
    import_jobs: Mapped[list["ImportJob"]] = relationship(back_populates="workspace")
    categories: Mapped[list["Category"]] = relationship(back_populates="workspace")
    tags: Mapped[list["Tag"]] = relationship(back_populates="workspace")
    transactions: Mapped[list["Transaction"]] = relationship(back_populates="workspace")
    merchant_rules: Mapped[list["MerchantRule"]] = relationship(back_populates="workspace")
    rule_application_runs: Mapped[list["RuleApplicationRun"]] = relationship(
        back_populates="workspace"
    )
    payslips: Mapped[list["Payslip"]] = relationship(back_populates="workspace")
    income_records: Mapped[list["IncomeRecord"]] = relationship(back_populates="workspace")
    budgets: Mapped[list["Budget"]] = relationship(back_populates="workspace")
    savings_goals: Mapped[list["SavingsGoal"]] = relationship(back_populates="workspace")
    insight_snapshots: Mapped[list["InsightSnapshot"]] = relationship(back_populates="workspace")
    accounts: Mapped[list["Account"]] = relationship(back_populates="workspace")
    account_balance_snapshots: Mapped[list["AccountBalanceSnapshot"]] = relationship(
        back_populates="workspace"
    )
    statement_imports: Mapped[list["AccountStatementImport"]] = relationship(
        back_populates="workspace"
    )


class WorkspaceMembership(Base):
    __tablename__ = "workspace_memberships"
    __table_args__ = (UniqueConstraint("workspace_id", "user_id", name="uix_workspace_user"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    workspace_id: Mapped[int] = mapped_column(ForeignKey("workspaces.id"), index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    role: Mapped[str] = mapped_column(String(50), default="member")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    workspace: Mapped["Workspace"] = relationship(back_populates="memberships")
    user: Mapped["User"] = relationship(back_populates="memberships")


class WorkspaceInvitation(Base):
    __tablename__ = "workspace_invitations"

    id: Mapped[int] = mapped_column(primary_key=True)
    workspace_id: Mapped[int] = mapped_column(ForeignKey("workspaces.id"), index=True)
    email: Mapped[str] = mapped_column(String(320), index=True)
    token: Mapped[str] = mapped_column(String(128), unique=True, index=True)
    invited_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    accepted: Mapped[bool] = mapped_column(Boolean, default=False)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    workspace: Mapped["Workspace"] = relationship(back_populates="invitations")
    invited_by: Mapped["User | None"] = relationship(back_populates="sent_invitations")


class UploadedFile(Base):
    __tablename__ = "uploaded_files"

    id: Mapped[int] = mapped_column(primary_key=True)
    workspace_id: Mapped[int] = mapped_column(ForeignKey("workspaces.id"), index=True)
    file_type: Mapped[str] = mapped_column(String(50))
    storage_path: Mapped[str] = mapped_column(String(512))
    checksum: Mapped[str] = mapped_column(String(64))
    size_bytes: Mapped[int] = mapped_column()
    retention_choice: Mapped[str] = mapped_column(String(20), default="retain")
    deleted: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    workspace: Mapped["Workspace"] = relationship(back_populates="uploaded_files")
    import_jobs: Mapped[list["ImportJob"]] = relationship(back_populates="uploaded_file")
    payslips: Mapped[list["Payslip"]] = relationship(back_populates="uploaded_file")
    account_balance_snapshots: Mapped[list["AccountBalanceSnapshot"]] = relationship(
        back_populates="uploaded_file"
    )
    statement_imports: Mapped[list["AccountStatementImport"]] = relationship(
        back_populates="uploaded_file"
    )


class ImportJob(Base):
    __tablename__ = "import_jobs"

    id: Mapped[int] = mapped_column(primary_key=True)
    workspace_id: Mapped[int] = mapped_column(ForeignKey("workspaces.id"), index=True)
    uploaded_file_id: Mapped[int | None] = mapped_column(ForeignKey("uploaded_files.id"))
    account_id: Mapped[int | None] = mapped_column(ForeignKey("accounts.id"))
    status: Mapped[str] = mapped_column(String(50), default="pending")
    column_mapping: Mapped[dict | None] = mapped_column(JSON)
    validation_errors: Mapped[dict | None] = mapped_column(JSON)
    source_checksum: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    workspace: Mapped["Workspace"] = relationship(back_populates="import_jobs")
    uploaded_file: Mapped["UploadedFile | None"] = relationship(back_populates="import_jobs")
    account: Mapped["Account | None"] = relationship(back_populates="import_jobs")
    transactions: Mapped[list["Transaction"]] = relationship(back_populates="import_job")


class Account(Base):
    __tablename__ = "accounts"
    __table_args__ = (
        CheckConstraint(
            "institution_key IS NULL OR institution_key IN "
            "('chase', 'bank_of_america', 'citi', 'capital_one', "
            "'american_express', 'discover', 'wells_fargo', 'other')",
            name="ck_accounts_institution_key",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    workspace_id: Mapped[int] = mapped_column(ForeignKey("workspaces.id"), index=True)
    name: Mapped[str] = mapped_column(String(255))
    account_type: Mapped[str] = mapped_column(String(50))
    institution_key: Mapped[str | None] = mapped_column(String(50))
    institution: Mapped[str | None] = mapped_column(String(255))
    is_liability: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    workspace: Mapped["Workspace"] = relationship(back_populates="accounts")
    balance_snapshots: Mapped[list["AccountBalanceSnapshot"]] = relationship(
        back_populates="account"
    )
    import_jobs: Mapped[list["ImportJob"]] = relationship(back_populates="account")
    statement_imports: Mapped[list["AccountStatementImport"]] = relationship(
        back_populates="account"
    )


class AccountStatementImport(Base):
    __tablename__ = "account_statement_imports"
    __table_args__ = (
        UniqueConstraint(
            "workspace_id",
            "statement_category",
            "source_checksum",
            name="uix_statement_import_workspace_category_checksum",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    workspace_id: Mapped[int] = mapped_column(ForeignKey("workspaces.id"), index=True)
    uploaded_file_id: Mapped[int] = mapped_column(ForeignKey("uploaded_files.id"))
    account_id: Mapped[int | None] = mapped_column(ForeignKey("accounts.id"))
    statement_category: Mapped[str] = mapped_column(String(50))
    source_checksum: Mapped[str] = mapped_column(String(64))
    candidate_fields: Mapped[dict] = mapped_column(JSON)
    confirmed_fields: Mapped[dict | None] = mapped_column(JSON)
    review_status: Mapped[str] = mapped_column(String(50), default="pending")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    workspace: Mapped["Workspace"] = relationship(back_populates="statement_imports")
    uploaded_file: Mapped["UploadedFile"] = relationship(back_populates="statement_imports")
    account: Mapped["Account | None"] = relationship(back_populates="statement_imports")
    balance_snapshot: Mapped["AccountBalanceSnapshot | None"] = relationship(
        back_populates="statement_import", uselist=False
    )


class AccountBalanceSnapshot(Base):
    __tablename__ = "account_balance_snapshots"
    __table_args__ = (
        Index("ix_workspace_balance_snapshot_date", "workspace_id", "as_of_date"),
        Index("ix_account_balance_snapshot_date", "account_id", "as_of_date"),
        Index(
            "uix_balance_snapshot_statement_import_id",
            "statement_import_id",
            unique=True,
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    workspace_id: Mapped[int] = mapped_column(ForeignKey("workspaces.id"))
    account_id: Mapped[int] = mapped_column(ForeignKey("accounts.id"))
    balance_cents: Mapped[int] = mapped_column()
    as_of_date: Mapped[date] = mapped_column(Date)
    source: Mapped[str] = mapped_column(String(50), default="manual")
    uploaded_file_id: Mapped[int | None] = mapped_column(ForeignKey("uploaded_files.id"))
    statement_import_id: Mapped[int | None] = mapped_column(
        ForeignKey("account_statement_imports.id")
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    workspace: Mapped["Workspace"] = relationship(back_populates="account_balance_snapshots")
    account: Mapped["Account"] = relationship(back_populates="balance_snapshots")
    uploaded_file: Mapped["UploadedFile | None"] = relationship(
        back_populates="account_balance_snapshots"
    )
    statement_import: Mapped["AccountStatementImport | None"] = relationship(
        back_populates="balance_snapshot"
    )


class Category(Base):
    __tablename__ = "categories"
    __table_args__ = (
        Index(
            "uix_custom_category_name_key",
            "workspace_id",
            "name_key",
            unique=True,
            sqlite_where=text("workspace_id IS NOT NULL"),
            postgresql_where=text("workspace_id IS NOT NULL"),
        ),
        Index(
            "uix_builtin_category_name_key",
            "name_key",
            unique=True,
            sqlite_where=text("workspace_id IS NULL"),
            postgresql_where=text("workspace_id IS NULL"),
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    workspace_id: Mapped[int | None] = mapped_column(ForeignKey("workspaces.id"), index=True)
    name: Mapped[str] = mapped_column(String(100))
    name_key: Mapped[str] = mapped_column(String(100), default=_default_category_name_key)
    kind: Mapped[str] = mapped_column(String(20), default="expense")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    workspace: Mapped["Workspace | None"] = relationship(back_populates="categories")
    transactions: Mapped[list["Transaction"]] = relationship(back_populates="category")
    merchant_rules: Mapped[list["MerchantRule"]] = relationship(back_populates="category")
    budgets: Mapped[list["Budget"]] = relationship(back_populates="category")


class Tag(Base):
    __tablename__ = "tags"
    __table_args__ = (
        Index(
            "uix_custom_tag_name_key",
            "workspace_id",
            "name_key",
            unique=True,
            sqlite_where=text("workspace_id IS NOT NULL"),
            postgresql_where=text("workspace_id IS NOT NULL"),
        ),
        Index(
            "uix_builtin_tag_name_key",
            "name_key",
            unique=True,
            sqlite_where=text("workspace_id IS NULL"),
            postgresql_where=text("workspace_id IS NULL"),
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    workspace_id: Mapped[int | None] = mapped_column(ForeignKey("workspaces.id"), index=True)
    name: Mapped[str] = mapped_column(String(100))
    name_key: Mapped[str] = mapped_column(String(100), default=_default_category_name_key)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    workspace: Mapped["Workspace | None"] = relationship(back_populates="tags")
    transactions: Mapped[list["Transaction"]] = relationship(
        secondary=transaction_tags,
        back_populates="tags",
    )
    merchant_rules: Mapped[list["MerchantRule"]] = relationship(
        secondary=merchant_rule_tags,
        back_populates="tags",
    )


class Transaction(Base):
    __tablename__ = "transactions"
    __table_args__ = (
        UniqueConstraint(
            "workspace_id", "duplicate_fingerprint", name="uix_workspace_duplicate_fingerprint"
        ),
        Index("ix_workspace_transaction_date", "workspace_id", "date"),
        Index("ix_workspace_transaction_category", "workspace_id", "category_id"),
        Index("ix_workspace_normalized_merchant", "workspace_id", "normalized_merchant"),
        Index("ix_transactions_merchant_rule_id", "merchant_rule_id"),
        CheckConstraint(
            "billing_period_months IS NULL OR "
            "(billing_period_months >= 1 AND billing_period_months <= 120)",
            name="ck_transactions_billing_period_months",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    workspace_id: Mapped[int] = mapped_column(ForeignKey("workspaces.id"), index=True)
    date: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    description: Mapped[str] = mapped_column(String(512))
    normalized_merchant: Mapped[str | None] = mapped_column(String(255))
    amount_cents: Mapped[int] = mapped_column()
    category_id: Mapped[int | None] = mapped_column(ForeignKey("categories.id"), index=True)
    merchant_rule_id: Mapped[int | None] = mapped_column(
        ForeignKey("merchant_rules.id", ondelete="SET NULL")
    )
    categorization_source: Mapped[str] = mapped_column(String(50), default="uncategorized")
    is_subscription: Mapped[bool] = mapped_column(Boolean, default=False, server_default=false())
    billing_period_months: Mapped[int | None] = mapped_column(Integer)
    duplicate_fingerprint: Mapped[str | None] = mapped_column(String(64))
    import_job_id: Mapped[int | None] = mapped_column(ForeignKey("import_jobs.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    workspace: Mapped["Workspace"] = relationship(back_populates="transactions")
    category: Mapped["Category | None"] = relationship(back_populates="transactions")
    merchant_rule: Mapped["MerchantRule | None"] = relationship(back_populates="transactions")
    import_job: Mapped["ImportJob | None"] = relationship(back_populates="transactions")
    tags: Mapped[list["Tag"]] = relationship(
        secondary=transaction_tags,
        back_populates="transactions",
        order_by="Tag.name_key",
    )


class MerchantRule(Base):
    __tablename__ = "merchant_rules"
    __table_args__ = (
        UniqueConstraint("workspace_id", "merchant_pattern", name="uix_workspace_merchant_pattern"),
        CheckConstraint(
            "billing_period_months IS NULL OR "
            "(billing_period_months >= 1 AND billing_period_months <= 120)",
            name="ck_merchant_rules_billing_period_months",
        ),
        CheckConstraint("priority >= 0", name="ck_merchant_rules_priority_nonnegative"),
        CheckConstraint("lock_version > 0", name="ck_merchant_rules_lock_version_positive"),
        CheckConstraint("condition_version = 1", name="ck_merchant_rules_condition_version_one"),
        Index(
            "ix_merchant_rules_workspace_enabled_priority",
            "workspace_id",
            "enabled",
            "priority",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    workspace_id: Mapped[int] = mapped_column(ForeignKey("workspaces.id"), index=True)
    merchant_pattern: Mapped[str | None] = mapped_column(String(255))
    name: Mapped[str] = mapped_column(String(120), server_default=text("''"))
    enabled: Mapped[bool] = mapped_column(Boolean, server_default=true())
    priority: Mapped[int] = mapped_column(Integer, server_default=text("0"))
    condition_version: Mapped[int] = mapped_column(Integer, server_default=text("1"))
    condition_json: Mapped[dict] = mapped_column(JSON, server_default=text("'{}'"))
    lock_version: Mapped[int] = mapped_column(Integer, server_default=text("1"))
    normalized_merchant: Mapped[str | None] = mapped_column(String(255))
    category_id: Mapped[int | None] = mapped_column(ForeignKey("categories.id"), index=True)
    is_subscription: Mapped[bool] = mapped_column(Boolean, default=False, server_default=false())
    billing_period_months: Mapped[int | None] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    workspace: Mapped["Workspace"] = relationship(back_populates="merchant_rules")
    transactions: Mapped[list["Transaction"]] = relationship(back_populates="merchant_rule")
    category: Mapped["Category | None"] = relationship(back_populates="merchant_rules")
    tags: Mapped[list["Tag"]] = relationship(
        secondary=merchant_rule_tags,
        back_populates="merchant_rules",
        order_by="Tag.name_key",
    )
    application_runs: Mapped[list["RuleApplicationRun"]] = relationship(
        back_populates="merchant_rule"
    )


class RuleApplicationRun(Base):
    """Redacted audit metadata for one historical rule-application attempt."""

    __tablename__ = "rule_application_runs"
    __table_args__ = (
        CheckConstraint(
            "rule_lock_version > 0",
            name="ck_rule_application_runs_rule_lock_version_positive",
        ),
        CheckConstraint(
            "status IN ('previewed', 'confirmed', 'stale', 'failed')",
            name="ck_rule_application_runs_status",
        ),
        CheckConstraint(
            "matched_count >= 0 AND changed_count >= 0 AND unchanged_count >= 0 "
            "AND manual_skip_count >= 0 AND conflict_skip_count >= 0",
            name="ck_rule_application_runs_counts_nonnegative",
        ),
        CheckConstraint(
            "(status = 'confirmed' AND confirmed_at IS NOT NULL) OR "
            "(status <> 'confirmed' AND confirmed_at IS NULL)",
            name="ck_rule_application_runs_confirmation_state",
        ),
        CheckConstraint(
            "length(preview_digest) = 64",
            name="ck_rule_application_runs_preview_digest_length",
        ),
        CheckConstraint(
            "preview_digest = lower(preview_digest)",
            name="ck_rule_application_runs_preview_digest_lowercase",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    workspace_id: Mapped[int] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), index=True
    )
    merchant_rule_id: Mapped[int | None] = mapped_column(
        ForeignKey("merchant_rules.id", ondelete="SET NULL"), index=True
    )
    initiated_by_user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    rule_name_snapshot: Mapped[str] = mapped_column(String(120))
    rule_lock_version: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(16))
    selection_json: Mapped[Mapping[str, object]] = mapped_column(ApplicationSelectionJSON())
    preview_digest: Mapped[str] = mapped_column(String(64), index=True)
    matched_count: Mapped[int] = mapped_column(Integer, default=0, server_default=text("0"))
    changed_count: Mapped[int] = mapped_column(Integer, default=0, server_default=text("0"))
    unchanged_count: Mapped[int] = mapped_column(Integer, default=0, server_default=text("0"))
    manual_skip_count: Mapped[int] = mapped_column(Integer, default=0, server_default=text("0"))
    conflict_skip_count: Mapped[int] = mapped_column(Integer, default=0, server_default=text("0"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    workspace: Mapped["Workspace"] = relationship(back_populates="rule_application_runs")
    merchant_rule: Mapped["MerchantRule | None"] = relationship(back_populates="application_runs")
    initiated_by_user: Mapped["User"] = relationship(
        back_populates="initiated_rule_application_runs"
    )

    @validates("preview_digest")
    def _validate_preview_digest(self, _key: str, value: object) -> str:
        return validate_application_digest(value)


class Payslip(Base):
    __tablename__ = "payslips"

    id: Mapped[int] = mapped_column(primary_key=True)
    workspace_id: Mapped[int] = mapped_column(ForeignKey("workspaces.id"), index=True)
    uploaded_file_id: Mapped[int | None] = mapped_column(ForeignKey("uploaded_files.id"))
    employer: Mapped[str | None] = mapped_column(String(255))
    pay_period_start: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    pay_period_end: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    pay_date: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    candidate_fields: Mapped[dict | None] = mapped_column(JSON)
    confidence: Mapped[float | None] = mapped_column(Float)
    review_status: Mapped[str] = mapped_column(String(50), default="pending")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    workspace: Mapped["Workspace"] = relationship(back_populates="payslips")
    uploaded_file: Mapped["UploadedFile | None"] = relationship(back_populates="payslips")
    income_records: Mapped[list["IncomeRecord"]] = relationship(back_populates="payslip")


class IncomeRecord(Base):
    __tablename__ = "income_records"
    __table_args__ = (Index("uix_income_records_payslip_id", "payslip_id", unique=True),)

    id: Mapped[int] = mapped_column(primary_key=True)
    workspace_id: Mapped[int] = mapped_column(ForeignKey("workspaces.id"), index=True)
    payslip_id: Mapped[int | None] = mapped_column(ForeignKey("payslips.id"))
    employer: Mapped[str | None] = mapped_column(String(255))
    pay_date: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    gross_pay_cents: Mapped[int] = mapped_column()
    net_pay_cents: Mapped[int] = mapped_column()
    taxes_cents: Mapped[int] = mapped_column()
    deductions_cents: Mapped[int] = mapped_column()
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    workspace: Mapped["Workspace"] = relationship(back_populates="income_records")
    payslip: Mapped["Payslip | None"] = relationship(back_populates="income_records")


class Budget(Base):
    __tablename__ = "budgets"
    __table_args__ = (
        UniqueConstraint(
            "workspace_id", "category_id", "period_month", name="uix_workspace_category_month"
        ),
        Index("ix_workspace_budget_period", "workspace_id", "period_month"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    workspace_id: Mapped[int] = mapped_column(ForeignKey("workspaces.id"), index=True)
    category_id: Mapped[int] = mapped_column(ForeignKey("categories.id"), index=True)
    amount_cents: Mapped[int] = mapped_column()
    period_month: Mapped[date] = mapped_column(Date)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    workspace: Mapped["Workspace"] = relationship(back_populates="budgets")
    category: Mapped["Category"] = relationship(back_populates="budgets")


class SavingsGoal(Base):
    __tablename__ = "savings_goals"

    id: Mapped[int] = mapped_column(primary_key=True)
    workspace_id: Mapped[int] = mapped_column(ForeignKey("workspaces.id"), index=True)
    name: Mapped[str] = mapped_column(String(255))
    target_amount_cents: Mapped[int] = mapped_column()
    current_amount_cents: Mapped[int] = mapped_column(default=0)
    target_date: Mapped[date | None] = mapped_column(Date)
    monthly_contribution_cents: Mapped[int | None] = mapped_column()
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    workspace: Mapped["Workspace"] = relationship(back_populates="savings_goals")


class InsightSnapshot(Base):
    __tablename__ = "insight_snapshots"
    __table_args__ = (Index("ix_workspace_insight_period", "workspace_id", "period_start"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    workspace_id: Mapped[int] = mapped_column(ForeignKey("workspaces.id"), index=True)
    period_start: Mapped[date] = mapped_column(Date)
    period_end: Mapped[date] = mapped_column(Date)
    snapshot_data: Mapped[dict] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    workspace: Mapped["Workspace"] = relationship(back_populates="insight_snapshots")
