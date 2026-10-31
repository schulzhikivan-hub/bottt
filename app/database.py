from sqlalchemy import create_engine, text
from sqlalchemy.orm import DeclarativeBase, sessionmaker
from app.config import settings

connect_args = {"check_same_thread": False} if settings.database_url.startswith("sqlite") else {}
engine = create_engine(settings.database_url, pool_pre_ping=True, connect_args=connect_args)
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)


class Base(DeclarativeBase):
    pass


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def migrate() -> None:
    """Small idempotent schema changes for databases created by earlier versions.

    create_all() does not alter existing tables, so these statements bring an
    existing PostgreSQL database in line with the models. SQLite (tests) is always fresh.
    """
    if engine.dialect.name != "postgresql":
        return
    with engine.begin() as conn:
        conn.execute(text("ALTER TABLE conversations ALTER COLUMN listing_id DROP NOT NULL"))
        conn.execute(text("ALTER TABLE conversations ADD COLUMN IF NOT EXISTS match_key VARCHAR(80)"))
        conn.execute(text("CREATE UNIQUE INDEX IF NOT EXISTS uq_conversations_match_key ON conversations (match_key)"))
        conn.execute(text("ALTER TABLE listings ADD COLUMN IF NOT EXISTS note VARCHAR(300) DEFAULT ''"))
        conn.execute(text("ALTER TABLE users ADD COLUMN IF NOT EXISTS employer_status VARCHAR(16) DEFAULT 'none'"))
        conn.execute(text("ALTER TABLE users ADD COLUMN IF NOT EXISTS company_name VARCHAR(160) DEFAULT ''"))
        conn.execute(text("ALTER TABLE users ADD COLUMN IF NOT EXISTS registration_no VARCHAR(80) DEFAULT ''"))
        conn.execute(text("ALTER TABLE users ADD COLUMN IF NOT EXISTS contact_person VARCHAR(80) DEFAULT ''"))
        conn.execute(text("ALTER TABLE users ADD COLUMN IF NOT EXISTS block_reason VARCHAR(300) DEFAULT ''"))
        conn.execute(text("ALTER TABLE users ADD COLUMN IF NOT EXISTS notify_messages BOOLEAN DEFAULT TRUE"))
        for column in ("price_amount BIGINT", "housing_provided BOOLEAN", "meals_provided BOOLEAN",
                       "rooms INTEGER", "condition VARCHAR(10)", "search_text TEXT", "city_key VARCHAR(80)"):
            conn.execute(text(f"ALTER TABLE listings ADD COLUMN IF NOT EXISTS {column}"))
        conn.execute(text("CREATE INDEX IF NOT EXISTS ix_listings_price_amount ON listings (price_amount)"))
        conn.execute(text("CREATE INDEX IF NOT EXISTS ix_listings_city_key ON listings (city_key)"))
