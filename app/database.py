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


from contextlib import contextmanager


@contextmanager
def startup_lock():
    """Several web workers start at once: on PostgreSQL only one at a time may create tables, migrate and seed."""
    if engine.dialect.name != "postgresql":
        yield
        return
    with engine.connect() as conn:
        conn.execute(text("select pg_advisory_lock(727001)"))
        try:
            yield
        finally:
            conn.execute(text("select pg_advisory_unlock(727001)"))
            conn.commit()


def upgrade_schema(url: str | None = None) -> str:
    """Bring the schema to the latest Alembic revision. A database made before Alembic is only stamped, never rebuilt.

    Returns "stamped", "upgraded" or "skipped". On SQLite (tests, local runs) nothing happens unless a url is given."""
    from pathlib import Path
    from alembic import command
    from alembic.config import Config
    from sqlalchemy import create_engine, inspect
    target = url or settings.database_url
    if url is None and not target.startswith("postgresql"):
        return "skipped"
    root = Path(__file__).resolve().parent.parent
    if not (root / "alembic.ini").exists():
        return "skipped"
    cfg = Config(str(root / "alembic.ini"))
    cfg.set_main_option("script_location", str(root / "alembic"))
    cfg.attributes["url"] = target
    probe = create_engine(target)
    try:
        has_version = "alembic_version" in inspect(probe).get_table_names()
    finally:
        probe.dispose()
    if not has_version:
        command.stamp(cfg, "head")
        return "stamped"
    command.upgrade(cfg, "head")
    return "upgraded"


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
        conn.execute(text("ALTER TABLE users ADD COLUMN IF NOT EXISTS visa VARCHAR(8) DEFAULT ''"))
        for column in ("price_amount BIGINT", "housing_provided BOOLEAN", "meals_provided BOOLEAN",
                       "rooms INTEGER", "condition VARCHAR(10)", "search_text TEXT", "city_key VARCHAR(80)"):
            conn.execute(text(f"ALTER TABLE listings ADD COLUMN IF NOT EXISTS {column}"))
        for column in ("salary_period VARCHAR(8)", "visas VARCHAR(120)", "employment VARCHAR(10)", "schedule VARCHAR(10)",
                       "housing_type VARCHAR(12)", "deposit BIGINT", "area_m2 INTEGER", "pets_allowed BOOLEAN",
                       "furnished BOOLEAN", "goods_category VARCHAR(16)", "negotiable BOOLEAN", "details JSON"):
            conn.execute(text(f"ALTER TABLE listings ADD COLUMN IF NOT EXISTS {column}"))
        for column in ("published_at TIMESTAMPTZ", "expires_at TIMESTAMPTZ", "expiry_notified BOOLEAN DEFAULT FALSE",
                       "views INTEGER DEFAULT 0", "pending_changes JSON", "has_edit BOOLEAN DEFAULT FALSE",
                       "expiry_notified_1d BOOLEAN DEFAULT FALSE"):
            conn.execute(text(f"ALTER TABLE listings ADD COLUMN IF NOT EXISTS {column}"))
        conn.execute(text("CREATE INDEX IF NOT EXISTS ix_listings_expires_at ON listings (expires_at)"))
        conn.execute(text("ALTER TABLE applications ADD COLUMN IF NOT EXISTS reply VARCHAR(500) DEFAULT ''"))
        conn.execute(text("ALTER TABLE applications ADD COLUMN IF NOT EXISTS name VARCHAR(40) DEFAULT ''"))
        conn.execute(text("ALTER TABLE users ADD COLUMN IF NOT EXISTS consent_version VARCHAR(40) DEFAULT ''"))
        conn.execute(text("ALTER TABLE users ADD COLUMN IF NOT EXISTS blocked_until TIMESTAMPTZ"))
        for ddl in (
            "ALTER TABLE dating_profiles ADD COLUMN IF NOT EXISTS birth_date DATE",
            "ALTER TABLE dating_profiles ADD COLUMN IF NOT EXISTS seeking VARCHAR(4) NOT NULL DEFAULT ''",
            "ALTER TABLE dating_profiles ADD COLUMN IF NOT EXISTS goal VARCHAR(12) NOT NULL DEFAULT ''",
            "ALTER TABLE dating_profiles ADD COLUMN IF NOT EXISTS height INTEGER",
            "ALTER TABLE dating_profiles ADD COLUMN IF NOT EXISTS visa VARCHAR(8) NOT NULL DEFAULT ''",
            "ALTER TABLE dating_profiles ADD COLUMN IF NOT EXISTS invisible_until TIMESTAMPTZ",
            "ALTER TABLE dating_profiles ADD COLUMN IF NOT EXISTS invisible_on BOOLEAN NOT NULL DEFAULT FALSE",
            "ALTER TABLE dating_profiles ADD COLUMN IF NOT EXISTS suspect BOOLEAN NOT NULL DEFAULT FALSE",
            "ALTER TABLE dating_profiles ADD COLUMN IF NOT EXISTS vip_until TIMESTAMPTZ",
            "ALTER TABLE dating_profiles ADD COLUMN IF NOT EXISTS vip_promo BOOLEAN NOT NULL DEFAULT FALSE",
            "ALTER TABLE dating_profiles ADD COLUMN IF NOT EXISTS vip_checked_at TIMESTAMPTZ",
            "ALTER TABLE dating_profiles ADD COLUMN IF NOT EXISTS viewers_until TIMESTAMPTZ",
            "ALTER TABLE dating_profiles ADD COLUMN IF NOT EXISTS super_credits INTEGER NOT NULL DEFAULT 0",
            "ALTER TABLE dating_likes ADD COLUMN IF NOT EXISTS created_at TIMESTAMPTZ",
            "ALTER TABLE dating_likes ADD COLUMN IF NOT EXISTS notif_id INTEGER",
            "ALTER TABLE dating_likes ADD COLUMN IF NOT EXISTS super BOOLEAN NOT NULL DEFAULT FALSE",
        ):
            conn.execute(text(ddl))
        for ddl in (
            "ALTER TABLE payments ALTER COLUMN listing_id DROP NOT NULL",
            "ALTER TABLE payments ADD COLUMN IF NOT EXISTS profile_id INTEGER",
            "ALTER TABLE payments ADD COLUMN IF NOT EXISTS note VARCHAR(200) NOT NULL DEFAULT ''",
            "ALTER TABLE payments ADD COLUMN IF NOT EXISTS balance_used INTEGER NOT NULL DEFAULT 0",
            "ALTER TABLE users ADD COLUMN IF NOT EXISTS balance_stars INTEGER NOT NULL DEFAULT 0",
            "ALTER TABLE dating_profiles ADD COLUMN IF NOT EXISTS vip_notified VARCHAR(4) NOT NULL DEFAULT ''",
        ):
            conn.execute(text(ddl))
        conn.execute(text("ALTER TABLE placements ADD COLUMN IF NOT EXISTS promo BOOLEAN NOT NULL DEFAULT FALSE"))
        conn.execute(text("ALTER TABLE placements ADD COLUMN IF NOT EXISTS frozen_at TIMESTAMPTZ"))
        conn.execute(text("ALTER TABLE broadcasts ADD COLUMN IF NOT EXISTS audience_kind VARCHAR(12) NOT NULL DEFAULT 'all'"))
        conn.execute(text("ALTER TABLE broadcasts ADD COLUMN IF NOT EXISTS send_at TIMESTAMPTZ"))
        conn.execute(text("ALTER TABLE admin_sessions ADD COLUMN IF NOT EXISTS created_at TIMESTAMPTZ"))
        conn.execute(text("ALTER TABLE saved_searches ADD COLUMN IF NOT EXISTS paused BOOLEAN NOT NULL DEFAULT FALSE"))
        conn.execute(text("ALTER TABLE messages ADD COLUMN IF NOT EXISTS file_key VARCHAR(40)"))
        conn.execute(text("ALTER TABLE messages ADD COLUMN IF NOT EXISTS file_name VARCHAR(80) NOT NULL DEFAULT ''"))
        conn.execute(text("ALTER TABLE messages ADD COLUMN IF NOT EXISTS file_kind VARCHAR(8) NOT NULL DEFAULT ''"))
        conn.execute(text("ALTER TABLE dating_profiles ADD COLUMN IF NOT EXISTS paused BOOLEAN NOT NULL DEFAULT FALSE"))
        conn.execute(text("ALTER TABLE dating_profiles ADD COLUMN IF NOT EXISTS note VARCHAR(300) NOT NULL DEFAULT ''"))
        conn.execute(text("ALTER TABLE reviews ADD COLUMN IF NOT EXISTS tags VARCHAR(80) NOT NULL DEFAULT ''"))
        conn.execute(text("ALTER TABLE reviews ADD COLUMN IF NOT EXISTS reply VARCHAR(500) NOT NULL DEFAULT ''"))
        conn.execute(text("ALTER TABLE users ADD COLUMN IF NOT EXISTS ref_code VARCHAR(12)"))
        conn.execute(text("CREATE UNIQUE INDEX IF NOT EXISTS ix_users_ref_code ON users (ref_code)"))
        conn.execute(text("ALTER TABLE users ADD COLUMN IF NOT EXISTS trusted BOOLEAN NOT NULL DEFAULT FALSE"))
        conn.execute(text("ALTER TABLE listing_photos ADD COLUMN IF NOT EXISTS sha VARCHAR(64)"))
        conn.execute(text("CREATE INDEX IF NOT EXISTS ix_listing_photos_sha ON listing_photos (sha)"))
        conn.execute(text("ALTER TABLE payments ADD COLUMN IF NOT EXISTS promo_code VARCHAR(24) DEFAULT ''"))
        conn.execute(text("ALTER TABLE payments ADD COLUMN IF NOT EXISTS list_stars INTEGER"))
        conn.execute(text("ALTER TABLE placements ADD COLUMN IF NOT EXISTS reminded BOOLEAN DEFAULT FALSE"))
        conn.execute(text("ALTER TABLE dating_profiles ADD COLUMN IF NOT EXISTS selfie_key VARCHAR(80)"))
        conn.execute(text("ALTER TABLE dating_profiles ADD COLUMN IF NOT EXISTS selfie_status VARCHAR(10) DEFAULT ''"))
        conn.execute(text("ALTER TABLE dating_profiles ADD COLUMN IF NOT EXISTS first_name VARCHAR(40) DEFAULT ''"))
        conn.execute(text("ALTER TABLE listings ADD COLUMN IF NOT EXISTS year INTEGER"))
        conn.execute(text("ALTER TABLE listings ADD COLUMN IF NOT EXISTS mileage_km INTEGER"))
        conn.execute(text("ALTER TABLE listings ADD COLUMN IF NOT EXISTS reserved BOOLEAN DEFAULT FALSE"))
        conn.execute(text("CREATE INDEX IF NOT EXISTS ix_listings_year ON listings (year)"))
        conn.execute(text("ALTER TABLE groups ADD COLUMN IF NOT EXISTS kind VARCHAR(10) DEFAULT 'nation'"))
        conn.execute(text("UPDATE listings SET year = (details->>'year')::int WHERE year IS NULL AND (details->>'year') ~ '^[0-9]+$'"))
        conn.execute(text("UPDATE listings SET mileage_km = (details->>'mileage_km')::int WHERE mileage_km IS NULL AND (details->>'mileage_km') ~ '^[0-9]+$'"))
        conn.execute(text("ALTER TABLE users ADD COLUMN IF NOT EXISTS notify_prefs JSON"))
        conn.execute(text("ALTER TABLE users ADD COLUMN IF NOT EXISTS display_name VARCHAR(40) DEFAULT ''"))
        conn.execute(text("ALTER TABLE users ADD COLUMN IF NOT EXISTS avatar_key VARCHAR(40)"))
        conn.execute(text("ALTER TABLE users ADD COLUMN IF NOT EXISTS last_seen_at TIMESTAMPTZ"))
        conn.execute(text("ALTER TABLE messages ADD COLUMN IF NOT EXISTS photo_key VARCHAR(40)"))
        conn.execute(text("ALTER TABLE messages ADD COLUMN IF NOT EXISTS read_at TIMESTAMPTZ"))
        conn.execute(text("CREATE INDEX IF NOT EXISTS ix_listings_has_edit ON listings (has_edit)"))
        # Listings published before expiry dates existed get 30 days from now
        conn.execute(text("UPDATE listings SET expires_at = NOW() + INTERVAL '30 days', published_at = created_at "
                          "WHERE status = 'approved' AND expires_at IS NULL"))
        conn.execute(text("CREATE INDEX IF NOT EXISTS ix_listings_price_amount ON listings (price_amount)"))
        conn.execute(text("CREATE INDEX IF NOT EXISTS ix_listings_city_key ON listings (city_key)"))
        conn.execute(text("ALTER TABLE listings ALTER COLUMN city TYPE VARCHAR(100)"))
        conn.execute(text("ALTER TABLE listings ADD COLUMN IF NOT EXISTS region_code VARCHAR(20)"))
        conn.execute(text("ALTER TABLE listings ADD COLUMN IF NOT EXISTS district_id INTEGER"))
        conn.execute(text("CREATE INDEX IF NOT EXISTS ix_listings_region_code ON listings (region_code)"))
        conn.execute(text("CREATE INDEX IF NOT EXISTS ix_listings_district_id ON listings (district_id)"))
        conn.execute(text("ALTER TABLE users ALTER COLUMN city TYPE VARCHAR(100)"))
        conn.execute(text("ALTER TABLE users ADD COLUMN IF NOT EXISTS region_code VARCHAR(20)"))
        conn.execute(text("ALTER TABLE users ADD COLUMN IF NOT EXISTS district_id INTEGER"))
        conn.execute(text("ALTER TABLE dating_profiles ALTER COLUMN city TYPE VARCHAR(100)"))
        conn.execute(text("ALTER TABLE dating_profiles ADD COLUMN IF NOT EXISTS region_code VARCHAR(20)"))
        conn.execute(text("ALTER TABLE dating_profiles ADD COLUMN IF NOT EXISTS district_id INTEGER"))
        conn.execute(text("ALTER TABLE listings ADD COLUMN IF NOT EXISTS expiry_notified_7d BOOLEAN NOT NULL DEFAULT FALSE"))
        conn.execute(text("ALTER TABLE dating_profiles ADD COLUMN IF NOT EXISTS bumped_until TIMESTAMPTZ"))
        conn.execute(text("ALTER TABLE listings ADD COLUMN IF NOT EXISTS mod_nudge INTEGER NOT NULL DEFAULT 0"))
        conn.execute(text("ALTER TABLE ads ADD COLUMN IF NOT EXISTS max_shows INTEGER"))
        conn.execute(text("ALTER TABLE listings ADD COLUMN IF NOT EXISTS company_id INTEGER"))
        conn.execute(text("ALTER TABLE payments ADD COLUMN IF NOT EXISTS company_id INTEGER"))
        for ddl in ("email VARCHAR(160)", "email_verified BOOLEAN NOT NULL DEFAULT FALSE", "password_hash VARCHAR(200)",
                    "password_set_at TIMESTAMPTZ", "pw_fails INTEGER NOT NULL DEFAULT 0", "pw_locked_until TIMESTAMPTZ",
                    "signup_skipped BOOLEAN NOT NULL DEFAULT FALSE"):
            conn.execute(text(f"ALTER TABLE users ADD COLUMN IF NOT EXISTS {ddl}"))
        conn.execute(text("CREATE UNIQUE INDEX IF NOT EXISTS ix_users_email ON users (email)"))
        conn.execute(text("ALTER TABLE companies ADD COLUMN IF NOT EXISTS logo_key VARCHAR(40)"))
        conn.execute(text("ALTER TABLE payments ADD COLUMN IF NOT EXISTS refunded_amount INTEGER NOT NULL DEFAULT 0"))
        conn.execute(text("ALTER TABLE listings ADD COLUMN IF NOT EXISTS quarantine BOOLEAN NOT NULL DEFAULT FALSE"))
        conn.execute(text("ALTER TABLE listings ADD COLUMN IF NOT EXISTS dispute BOOLEAN NOT NULL DEFAULT FALSE"))
        for ddl in ("title_b VARCHAR(80) NOT NULL DEFAULT ''", "text_b VARCHAR(200) NOT NULL DEFAULT ''", "shows_b INTEGER NOT NULL DEFAULT 0",
                    "clicks_b INTEGER NOT NULL DEFAULT 0", "winner VARCHAR(1) NOT NULL DEFAULT ''"):
            conn.execute(text(f"ALTER TABLE ads ADD COLUMN IF NOT EXISTS {ddl}"))
        conn.execute(text("ALTER TABLE notifications ADD COLUMN IF NOT EXISTS tg_pending BOOLEAN NOT NULL DEFAULT FALSE"))
