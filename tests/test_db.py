import pytest

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db.models import Base, User


@pytest.mark.parametrize("url", ["sqlite:///:memory:"])
def test_user_roundtrip(url: str) -> None:
    """Create tables in an in-memory SQLite DB and round-trip a User record."""
    engine = create_engine(url, future=True)
    Base.metadata.create_all(engine)

    Session = sessionmaker(bind=engine, future=True)
    with Session() as session:
        u = User(google_sub="sub-123", email="test@example.com", display_name="Test")
        session.add(u)
        session.commit()
        assert u.id is not None

        fetched = session.get(User, u.id)
        assert fetched is not None
        assert fetched.email == "test@example.com"
