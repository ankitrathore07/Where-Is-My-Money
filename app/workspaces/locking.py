"""Shared per-workspace serialization for cross-domain mutations."""

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.db.models import Workspace


def serialize_workspace_mutation(session: Session, workspace_id: int) -> bool:
    """Acquire the workspace writer key before reading mutation-sensitive state."""
    bind = session.get_bind()
    if bind.dialect.name == "sqlite":
        result = session.execute(
            update(Workspace)
            .where(Workspace.id == workspace_id)
            .values(name=Workspace.name)
            .execution_options(synchronize_session=False)
        )
        return result.rowcount == 1
    return (
        session.scalar(select(Workspace.id).where(Workspace.id == workspace_id).with_for_update())
        is not None
    )
