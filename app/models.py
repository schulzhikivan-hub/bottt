from datetime import datetime, timezone
from sqlalchemy import String, Text, DateTime, Integer, ForeignKey, UniqueConstraint, Boolean
from sqlalchemy.orm import Mapped, mapped_column
from app.database import Base


def _now():
    return datetime.now(timezone.utc)


class User(Base):
    __tablename__ = "users"
    tg_id: Mapped[str] = mapped_column(String(40), primary_key=True)
    language: Mapped[str] = mapped_column(String(8), default="ru")
    city: Mapped[str] = mapped_column(String(80), default="Сеул")
    consent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    blocked: Mapped[bool] = mapped_column(Boolean, default=False)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # none, pending, approved, rejected
    employer_status: Mapped[str] = mapped_column(String(16), default="none")
    company_name: Mapped[str] = mapped_column(String(160), default="")
    registration_no: Mapped[str] = mapped_column(String(80), default="")
    contact_person: Mapped[str] = mapped_column(String(80), default="")
    block_reason: Mapped[str] = mapped_column(String(300), default="")
    notify_messages: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class AuditLog(Base):
    """Journal of administrator actions (ТЗ § 10, § 16.3). Append-only in the application."""
    __tablename__ = "audit_logs"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    actor: Mapped[str] = mapped_column(String(80), index=True)
    action: Mapped[str] = mapped_column(String(60), index=True)
    object_type: Mapped[str] = mapped_column(String(30))
    object_id: Mapped[str] = mapped_column(String(40))
    details: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, index=True)


class Block(Base):
    """User-level block: blocker does not want contact with blocked."""
    __tablename__ = "blocks"
    __table_args__ = (UniqueConstraint("blocker_tg_id", "blocked_tg_id", name="uq_block"),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    blocker_tg_id: Mapped[str] = mapped_column(String(40), index=True)
    blocked_tg_id: Mapped[str] = mapped_column(String(40), index=True)


class Report(Base):
    __tablename__ = "reports"
    __table_args__ = (UniqueConstraint("reporter_tg_id", "target_type", "target_id", name="uq_report"),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    reporter_tg_id: Mapped[str] = mapped_column(String(40))
    target_type: Mapped[str] = mapped_column(String(16))
    target_id: Mapped[str] = mapped_column(String(40))
    reason: Mapped[str] = mapped_column(String(40))
    comment: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[str] = mapped_column(String(16), default="new", index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class Listing(Base):
    __tablename__ = "listings"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    category: Mapped[str] = mapped_column(String(30), index=True)
    title: Mapped[str] = mapped_column(String(160))
    city: Mapped[str] = mapped_column(String(80), index=True)
    price: Mapped[str] = mapped_column(String(80), default="")
    description: Mapped[str] = mapped_column(Text, default="")
    contact: Mapped[str] = mapped_column(String(160), default="")
    author_telegram_id: Mapped[str] = mapped_column(String(40), default="", index=True)
    # pending, approved, rejected, hidden (hidden = reports or author deletion)
    # pending, approved, rejected, needs_fix, hidden, closed, sold, archived
    status: Mapped[str] = mapped_column(String(20), default="pending", index=True)
    note: Mapped[str] = mapped_column(String(300), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class Favorite(Base):
    __tablename__ = "favorites"
    __table_args__ = (UniqueConstraint("user_tg_id", "listing_id", name="uq_favorite"),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_tg_id: Mapped[str] = mapped_column(String(40), index=True)
    listing_id: Mapped[int] = mapped_column(ForeignKey("listings.id", ondelete="CASCADE"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class Conversation(Base):
    """Chat about a listing (buyer and seller) or a dating match (match_key = "id1:id2")."""
    __tablename__ = "conversations"
    __table_args__ = (UniqueConstraint("listing_id", "buyer_tg_id", name="uq_conversation"),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    listing_id: Mapped[int | None] = mapped_column(
        ForeignKey("listings.id", ondelete="CASCADE"), index=True, nullable=True)
    buyer_tg_id: Mapped[str] = mapped_column(String(40), index=True)
    seller_tg_id: Mapped[str] = mapped_column(String(40), index=True)
    match_key: Mapped[str | None] = mapped_column(String(80), unique=True, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class DatingProfile(Base):
    """Dating profile. Visible only after moderation; contacts are never shown."""
    __tablename__ = "dating_profiles"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    owner_tg_id: Mapped[str] = mapped_column(String(40), unique=True, index=True)
    birth_year: Mapped[int] = mapped_column(Integer)
    gender: Mapped[str] = mapped_column(String(1))  # f or m
    city: Mapped[str] = mapped_column(String(80), index=True)
    bio: Mapped[str] = mapped_column(Text, default="")
    photo_key: Mapped[str | None] = mapped_column(String(80), nullable=True)
    # pending, published, rejected
    status: Mapped[str] = mapped_column(String(16), default="pending", index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class DatingLike(Base):
    __tablename__ = "dating_likes"
    __table_args__ = (UniqueConstraint("from_tg", "to_tg", name="uq_dating_like"),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    from_tg: Mapped[str] = mapped_column(String(40), index=True)
    to_tg: Mapped[str] = mapped_column(String(40), index=True)


class Message(Base):
    __tablename__ = "messages"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    conversation_id: Mapped[int] = mapped_column(ForeignKey("conversations.id", ondelete="CASCADE"), index=True)
    sender_tg_id: Mapped[str] = mapped_column(String(40))
    text: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, index=True)


class Pricing(Base):
    """Paid services (ТЗ № 2). Prices are editable by administrators."""
    __tablename__ = "pricing"
    code: Mapped[str] = mapped_column(String(40), primary_key=True)  # e.g. job_vip
    category: Mapped[str] = mapped_column(String(30))
    kind: Mapped[str] = mapped_column(String(16))  # bump, vip, pin, highlight
    title: Mapped[str] = mapped_column(String(80))
    stars: Mapped[int] = mapped_column(Integer)
    duration_days: Mapped[int] = mapped_column(Integer)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)


class Placement(Base):
    """An active paid placement (VIP, pin, bump, highlight) on a listing."""
    __tablename__ = "placements"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    listing_id: Mapped[int] = mapped_column(ForeignKey("listings.id", ondelete="CASCADE"), index=True)
    kind: Mapped[str] = mapped_column(String(16))
    starts_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    ends_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    status: Mapped[str] = mapped_column(String(16), default="active", index=True)  # active, ended, refunded
    payment_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    notified_end: Mapped[bool] = mapped_column(Boolean, default=False)


class Payment(Base):
    __tablename__ = "payments"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_tg_id: Mapped[str] = mapped_column(String(40), index=True)
    listing_id: Mapped[int] = mapped_column(ForeignKey("listings.id", ondelete="CASCADE"), index=True)
    service_code: Mapped[str] = mapped_column(String(40))
    stars: Mapped[int] = mapped_column(Integer)
    payload: Mapped[str] = mapped_column(String(64), unique=True)
    telegram_payment_charge_id: Mapped[str | None] = mapped_column(String(128), unique=True, nullable=True)
    # pending, paid, refunded, failed
    status: Mapped[str] = mapped_column(String(16), default="pending", index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    paid_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class Group(Base):
    __tablename__ = "groups"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(120))
    flag: Mapped[str] = mapped_column(String(8), default="")
    telegram_link: Mapped[str] = mapped_column(String(255), default="")
    description: Mapped[str] = mapped_column(Text, default="")
    members_count: Mapped[int] = mapped_column(Integer, default=0)


class Article(Base):
    """Guide and info articles. Shown only when published with a checked date."""
    __tablename__ = "articles"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    section: Mapped[str] = mapped_column(String(40), index=True)
    title: Mapped[str] = mapped_column(String(200))
    body: Mapped[str] = mapped_column(Text, default="")
    source_url: Mapped[str] = mapped_column(String(255), default="")
    checked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    published: Mapped[bool] = mapped_column(Boolean, default=False, index=True)


class Admin(Base):
    """Staff account. Role: moderator, admin or super_admin. Sign-in is by a code sent to Telegram."""
    __tablename__ = "admins"
    tg_id: Mapped[str] = mapped_column(String(40), primary_key=True)
    role: Mapped[str] = mapped_column(String(20), default="moderator")
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class AdminCode(Base):
    __tablename__ = "admin_codes"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    tg_id: Mapped[str] = mapped_column(String(40), index=True)
    code_hash: Mapped[str] = mapped_column(String(64))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    attempts: Mapped[int] = mapped_column(Integer, default=0)


class AdminSession(Base):
    __tablename__ = "admin_sessions"
    token_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    tg_id: Mapped[str] = mapped_column(String(40), index=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class LoginRequest(Base):
    """Browser sign-in through the bot: the site shows a link, the user confirms it in Telegram."""
    __tablename__ = "login_requests"
    token_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    status: Mapped[str] = mapped_column(String(12), default="pending")  # pending, confirmed, rejected, used
    tg_id: Mapped[str | None] = mapped_column(String(40), nullable=True)
    ip: Mapped[str] = mapped_column(String(60), default="")
    agent: Mapped[str] = mapped_column(String(120), default="")
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class WebSession(Base):
    """Session of a user who signed in on the website (outside the Mini App)."""
    __tablename__ = "web_sessions"
    token_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    tg_id: Mapped[str] = mapped_column(String(40), index=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
