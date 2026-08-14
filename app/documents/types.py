from collections.abc import Mapping
from dataclasses import dataclass
from typing import Literal

DocumentProcessorKey = Literal["transaction_import", "payslip", "statement_balance"]


@dataclass(frozen=True)
class DocumentCategory:
    key: str
    label: str
    processor: DocumentProcessorKey | None
    content_types_by_suffix: Mapping[str, frozenset[str]]
    compatible_account_types: frozenset[str] = frozenset()

    @property
    def accepted_suffixes(self) -> frozenset[str]:
        return frozenset(self.content_types_by_suffix)


@dataclass(frozen=True)
class DocumentProcessResult:
    message: str
    next_url: str
    next_label: str

    def as_payload(self) -> dict[str, bool | str]:
        return {
            "ok": True,
            "message": self.message,
            "next_url": self.next_url,
            "next_label": self.next_label,
        }
