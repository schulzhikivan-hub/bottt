import sqlalchemy as sa
from sqlalchemy import create_engine, inspect, text

from app import database, models  # noqa: F401  (models register their tables)
from app.database import Base


def test_new_database_is_stamped_and_nothing_is_rebuilt(tmp_path):
    url = f"sqlite:///{tmp_path}/old.db"
    engine = create_engine(url)
    Base.metadata.create_all(engine)              # a database made before Alembic (create_all, as in production)
    with engine.begin() as conn:
        conn.execute(text("insert into users (tg_id, language, city, blocked, employer_status, company_name, registration_no, "
                          "contact_person, block_reason, notify_messages, visa, display_name, consent_version, created_at) values "
                          "('1','ru','Сеул',0,'none','','','','',1,'','Иван','', '2026-01-01')"))
    assert database.upgrade_schema(url) == "stamped"
    with engine.begin() as conn:
        assert conn.execute(text("select version_num from alembic_version")).scalar() == "0001"
        assert conn.execute(text("select display_name from users where tg_id='1'")).scalar() == "Иван"   # data untouched
    assert database.upgrade_schema(url) == "upgraded"           # the second run just applies what is missing (nothing now)
    assert "listings" in inspect(engine).get_table_names()


def test_empty_database_gets_the_head_revision(tmp_path):
    url = f"sqlite:///{tmp_path}/empty.db"
    assert database.upgrade_schema(url) == "stamped"


def test_sqlite_runs_skip_alembic():
    assert database.upgrade_schema() == "skipped"


def test_models_and_baseline_agree_on_the_revision_chain():
    from alembic.config import Config
    from alembic.script import ScriptDirectory
    from pathlib import Path
    root = Path(database.__file__).resolve().parent.parent
    cfg = Config(str(root / "alembic.ini"))
    cfg.set_main_option("script_location", str(root / "alembic"))
    script = ScriptDirectory.from_config(cfg)
    assert script.get_heads() == ["0001"]
