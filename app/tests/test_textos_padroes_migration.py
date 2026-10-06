import importlib.util
from pathlib import Path

from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import create_engine, event, inspect, text


def test_textos_padroes_migration_upgrade_and_downgrade(tmp_path):
    migration_path = (
        Path(__file__).resolve().parents[2]
        / "migrations"
        / "versions"
        / "c82f4a19d3b1_add_textos_padroes.py"
    )
    spec = importlib.util.spec_from_file_location("textos_padroes_migration", migration_path)
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)

    engine = create_engine(f"sqlite:///{tmp_path / 'migration.db'}")

    @event.listens_for(engine, "connect")
    def enable_sqlite_foreign_keys(connection, _record):
        connection.execute("PRAGMA foreign_keys=ON")

    with engine.begin() as connection:
        with Operations.context(MigrationContext.configure(connection)):
            migration.upgrade()

        inspector = inspect(connection)
        assert inspector.has_table("categorias_textos_padroes")
        assert inspector.has_table("textos_padroes")
        foreign_key = inspector.get_foreign_keys("textos_padroes")[0]
        assert foreign_key["options"]["ondelete"] == "SET NULL"

        connection.execute(
            text("INSERT INTO categorias_textos_padroes (id, nome) VALUES (1, 'Categoria')")
        )
        connection.execute(
            text(
                "INSERT INTO textos_padroes (id, titulo, conteudo, categoria_id) "
                "VALUES (1, 'Título', 'Conteúdo', 1)"
            )
        )
        connection.execute(text("DELETE FROM categorias_textos_padroes WHERE id = 1"))
        assert connection.execute(
            text("SELECT categoria_id FROM textos_padroes WHERE id = 1")
        ).scalar_one() is None

        with Operations.context(MigrationContext.configure(connection)):
            migration.downgrade()

        inspector = inspect(connection)
        assert not inspector.has_table("textos_padroes")
        assert not inspector.has_table("categorias_textos_padroes")
