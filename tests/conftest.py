import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.db.models import Base, User, Workspace


@pytest.fixture
def session():
    """Create an in-memory SQLite DB with all tables and yield a session."""
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine)
    with factory() as s:
        yield s


@pytest.fixture
def workspace(session: Session):
    """Create a user and a personal workspace owned by them; return the workspace."""
    owner = User(google_sub="sub-1", email="owner@example.com", display_name="Owner")
    session.add(owner)
    session.commit()

    ws = Workspace(name="Personal", is_personal=True, owner_id=owner.id)
    session.add(ws)
    session.commit()
    return ws


@pytest.fixture
def other_workspace(session: Session):
    """Create an unrelated owner and workspace for privacy-boundary tests."""
    owner = User(google_sub="sub-other", email="other@example.com", display_name="Other")
    session.add(owner)
    session.commit()

    ws = Workspace(name="Other Personal", is_personal=True, owner_id=owner.id)
    session.add(ws)
    session.commit()
    return ws
