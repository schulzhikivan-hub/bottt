import app.main  # noqa: F401  (registers every model on the metadata)
from sqlalchemy import create_engine, inspect, text

from app.database import Base, heal_schema


def test_missing_columns_are_added_to_an_old_table_and_nothing_else_changes(tmp_path):
    eng = create_engine(f"sqlite:///{tmp_path / 'old.db'}")
    Base.metadata.create_all(bind=eng)
    with eng.begin() as conn:                                   # an older server: the table exists without the cover column
        conn.execute(text("ALTER TABLE company_pages RENAME TO company_pages_old"))
        conn.execute(text("CREATE TABLE company_pages (company_id INTEGER PRIMARY KEY, tagline VARCHAR(120), telegram VARCHAR(80), "
                          "instagram VARCHAR(80), kakao VARCHAR(80), address VARCHAR(160), hours VARCHAR(80), benefits VARCHAR(200))"))
        conn.execute(text("INSERT INTO company_pages (company_id, tagline) VALUES (1, 'Привет')"))
    assert "cover_key" not in {c["name"] for c in inspect(eng).get_columns("company_pages")}
    assert heal_schema(eng) == ["company_pages.cover_key"]
    assert "cover_key" in {c["name"] for c in inspect(eng).get_columns("company_pages")}
    with eng.connect() as conn:
        assert conn.execute(text("SELECT tagline FROM company_pages WHERE company_id = 1")).scalar() == "Привет"      # data kept
    assert heal_schema(eng) == []                                # nothing more to do the second time
