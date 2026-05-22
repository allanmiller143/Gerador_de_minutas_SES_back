import pytest

from app import create_app
from app.models import db


@pytest.fixture
def client(monkeypatch, tmp_path):
    test_db = tmp_path / "test.db"
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{test_db}")

    app = create_app(
        {
            "TESTING": True,
            "SQLALCHEMY_DATABASE_URI": f"sqlite:///{test_db}",
        }
    )

    with app.test_client() as client:
        yield client

    with app.app_context():
        db.session.remove()
        db.drop_all()
