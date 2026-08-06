from datetime import datetime

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Index,
    String,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


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
    transactions: Mapped[list["Transaction"]] = relationship(back_populates="workspace")
    merchant_rules: Mapped[list["MerchantRule"]] = relationship(back_populates="workspace")
    payslips: Mapped[list["Payslip"]] = relationship(back_populates="workspace")
    income_records: Mapped[list["IncomeRecord"]] = relationship(back_populates="workspace")


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


class ImportJob(Base):
    __tablename__ = "import_jobs"

    id: Mapped[int] = mapped_column(primary_key=True)
    workspace_id: Mapped[int] = mapped_column(ForeignKey("workspaces.id"), index=True)
    uploaded_file_id: Mapped[int | None] = mapped_column(ForeignKey("uploaded_files.id"))
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
    transactions: Mapped[list["Transaction"]] = relationship(back_populates="import_job")


class Category(Base):
    __tablename__ = "categories"

    id: Mapped[int] = mapped_column(primary_key=True)
    workspace_id: Mapped[int | None] = mapped_column(ForeignKey("workspaces.id"), index=True)
    name: Mapped[str] = mapped_column(String(100))
    kind: Mapped[str] = mapped_column(String(20), default="expense")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    workspace: Mapped["Workspace | None"] = relationship(back_populates="categories")
    transactions: Mapped[list["Transaction"]] = relationship(back_populates="category")
    merchant_rules: Mapped[list["MerchantRule"]] = relationship(back_populates="category")


class Transaction(Base):
    __tablename__ = "transactions"
    __table_args__ = (
        UniqueConstraint(
            "workspace_id", "duplicate_fingerprint", name="uix_workspace_duplicate_fingerprint"
        ),
        Index("ix_workspace_transaction_date", "workspace_id", "date"),
        Index("ix_workspace_transaction_category", "workspace_id", "category_id"),
        Index("ix_workspace_normalized_merchant", "workspace_id", "normalized_merchant"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    workspace_id: Mapped[int] = mapped_column(ForeignKey("workspaces.id"), index=True)
    date: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    description: Mapped[str] = mapped_column(String(512))
    normalized_merchant: Mapped[str | None] = mapped_column(String(255))
    amount_cents: Mapped[int] = mapped_column()
    category_id: Mapped[int | None] = mapped_column(ForeignKey("categories.id"), index=True)
    categorization_source: Mapped[str] = mapped_column(String(50), default="uncategorized")
    duplicate_fingerprint: Mapped[str | None] = mapped_column(String(64))
    import_job_id: Mapped[int | None] = mapped_column(ForeignKey("import_jobs.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    workspace: Mapped["Workspace"] = relationship(back_populates="transactions")
    category: Mapped["Category | None"] = relationship(back_populates="transactions")
    import_job: Mapped["ImportJob | None"] = relationship(back_populates="transactions")


class MerchantRule(Base):
    __tablename__ = "merchant_rules"

    id: Mapped[int] = mapped_column(primary_key=True)
    workspace_id: Mapped[int] = mapped_column(ForeignKey("workspaces.id"), index=True)
    merchant_pattern: Mapped[str] = mapped_column(String(255))
    normalized_merchant: Mapped[str | None] = mapped_column(String(255))
    category_id: Mapped[int | None] = mapped_column(ForeignKey("categories.id"), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    workspace: Mapped["Workspace"] = relationship(back_populates="merchant_rules")
    category: Mapped["Category | None"] = relationship(back_populates="merchant_rules")


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
