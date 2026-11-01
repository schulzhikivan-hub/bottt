from datetime import date, datetime, timezone
from sqlalchemy import false, Date, String, Text, DateTime, Integer, BigInteger, ForeignKey, UniqueConstraint, Boolean, JSON, event
from sqlalchemy.orm import Mapped, mapped_column
from app.database import Base


def _now():
    return datetime.now(timezone.utc)


class User(Base):
    __tablename__ = "users"
    tg_id: Mapped[str] = mapped_column(String(40), primary_key=True)
    language: Mapped[str] = mapped_column(String(8), default="ru")
    city: Mapped[str] = mapped_column(String(100), default="Сеул")
    region_code: Mapped[str | None] = mapped_column(String(20), nullable=True)
    district_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
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
    notify_prefs: Mapped[dict | None] = mapped_column(JSON, nullable=True)  # {"messages": false, ...}; missing = on
    display_name: Mapped[str] = mapped_column(String(40), default="")
    avatar_key: Mapped[str | None] = mapped_column(String(40), nullable=True)
    last_seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    consent_version: Mapped[str] = mapped_column(String(40), default="")
    ref_code: Mapped[str | None] = mapped_column(String(12), unique=True, nullable=True)   # for the invitation link
    balance_stars: Mapped[int] = mapped_column(Integer, default=0, server_default="0")        # money back for unused service time
    blocked_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)   # temporary block ends here
    trusted: Mapped[bool] = mapped_column(Boolean, default=False, server_default=false())   # «Проверенный автор»: a moderator checked this person
    visa: Mapped[str] = mapped_column(String(8), default="")  # private; used for job filters and applications
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
    city: Mapped[str] = mapped_column(String(100), index=True)  # shown label, e.g. "Асан" or "Сеул, Каннам"
    region_code: Mapped[str | None] = mapped_column(String(20), nullable=True, index=True)
    district_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    price: Mapped[str] = mapped_column(String(80), default="")
    description: Mapped[str] = mapped_column(Text, default="")
    contact: Mapped[str] = mapped_column(String(160), default="")
    author_telegram_id: Mapped[str] = mapped_column(String(40), default="", index=True)
    # pending, approved, rejected, hidden (hidden = reports or author deletion)
    # draft, pending, approved, rejected, needs_fix, hidden, closed, sold, expired, archived
    status: Mapped[str] = mapped_column(String(20), default="pending", index=True)
    note: Mapped[str] = mapped_column(String(300), default="")
    # Searchable fields (filters and sorting need numbers and flags, not free text)
    price_amount: Mapped[int | None] = mapped_column(BigInteger, nullable=True, index=True)  # KRW
    housing_provided: Mapped[bool | None] = mapped_column(Boolean, nullable=True)  # job
    meals_provided: Mapped[bool | None] = mapped_column(Boolean, nullable=True)    # job
    rooms: Mapped[int | None] = mapped_column(Integer, nullable=True)              # housing
    condition: Mapped[str | None] = mapped_column(String(10), nullable=True)       # goods: new or used
    # More searchable details (see app/fields.py)
    salary_period: Mapped[str | None] = mapped_column(String(8), nullable=True)    # job: month, day, hour
    visas: Mapped[str | None] = mapped_column(String(120), nullable=True)          # job: ",E-9,F-4,"
    employment: Mapped[str | None] = mapped_column(String(10), nullable=True)      # job
    schedule: Mapped[str | None] = mapped_column(String(10), nullable=True)        # job
    housing_type: Mapped[str | None] = mapped_column(String(12), nullable=True)    # housing
    deposit: Mapped[int | None] = mapped_column(BigInteger, nullable=True)         # housing, KRW
    area_m2: Mapped[int | None] = mapped_column(Integer, nullable=True)            # housing
    pets_allowed: Mapped[bool | None] = mapped_column(Boolean, nullable=True)      # housing
    furnished: Mapped[bool | None] = mapped_column(Boolean, nullable=True)         # housing
    goods_category: Mapped[str | None] = mapped_column(String(16), nullable=True)  # goods
    negotiable: Mapped[bool | None] = mapped_column(Boolean, nullable=True)        # goods
    details: Mapped[dict | None] = mapped_column(JSON, nullable=True)              # descriptive, not filtered
    # Life cycle: draft -> pending -> approved -> (expired | closed | sold | archived)
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, index=True)
    company_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)     # posted on behalf of a company
    mod_nudge: Mapped[int] = mapped_column(Integer, default=0, server_default="0")        # moderation late: 1 = person told, 2 = staff alerted
    expiry_notified_7d: Mapped[bool] = mapped_column(Boolean, default=False, server_default=false())   # warned a week ahead
    expiry_notified: Mapped[bool] = mapped_column(Boolean, default=False)       # warned 3 days ahead
    expiry_notified_1d: Mapped[bool] = mapped_column(Boolean, default=False)    # warned 1 day ahead
    views: Mapped[int] = mapped_column(Integer, default=0)
    # Edits of a published listing wait for a moderator while the old version stays visible
    pending_changes: Mapped[dict | None] = mapped_column(JSON(none_as_null=True), nullable=True)
    has_edit: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    # Lower-case copies kept by the application, so search ignores case in any database locale
    year: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)       # cars: copied from details for filters
    mileage_km: Mapped[int | None] = mapped_column(Integer, nullable=True)
    reserved: Mapped[bool] = mapped_column(Boolean, default=False)  # goods, cars, services: "someone is already buying"
    search_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    city_key: Mapped[str | None] = mapped_column(String(80), nullable=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


@event.listens_for(Listing, "before_insert")
@event.listens_for(Listing, "before_update")
def _refresh_search_fields(mapper, connection, target):
    extra = [str(v) for k, v in (target.details or {}).items() if k in ("company", "position", "station", "brand")]
    target.search_text = " ".join([target.title or "", target.description or "", target.city or "", *extra]).lower()
    target.city_key = (target.city or "").strip().lower()
    d = target.details or {}
    target.year = d.get("year") if isinstance(d.get("year"), int) else None
    target.mileage_km = d.get("mileage_km") if isinstance(d.get("mileage_km"), int) else None


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
    city: Mapped[str] = mapped_column(String(100), index=True)
    region_code: Mapped[str | None] = mapped_column(String(20), nullable=True, index=True)
    district_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    first_name: Mapped[str] = mapped_column(String(40), default="")  # shown on the card; moderated with the profile
    bio: Mapped[str] = mapped_column(Text, default="")
    photo_key: Mapped[str | None] = mapped_column(String(80), nullable=True)
    # a second photo ("selfie with a gesture") that only moderators see; approved = the "verified" mark
    selfie_key: Mapped[str | None] = mapped_column(String(80), nullable=True)
    selfie_status: Mapped[str] = mapped_column(String(10), default="")  # "", pending, approved, rejected
    paused: Mapped[bool] = mapped_column(Boolean, default=False, server_default=false())   # hidden by the owner, no new moderation on return
    note: Mapped[str] = mapped_column(String(300), default="", server_default="")        # why a moderator rejected it
    birth_date: Mapped[date | None] = mapped_column(Date, nullable=True)                 # exact age; birth_year stays for old rows
    seeking: Mapped[str] = mapped_column(String(4), default="", server_default="")        # f | m | any
    goal: Mapped[str] = mapped_column(String(12), default="", server_default="")          # friendship | relationship | marriage | chat
    height: Mapped[int | None] = mapped_column(Integer, nullable=True)
    visa: Mapped[str] = mapped_column(String(8), default="", server_default="")
    invisible_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)   # paid service «невидимка»
    invisible_on: Mapped[bool] = mapped_column(Boolean, default=False, server_default=false())        # the owner switched it on
    suspect: Mapped[bool] = mapped_column(Boolean, default=False, server_default=false())             # hidden because the age or the person is doubtful
    vip_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    vip_promo: Mapped[bool] = mapped_column(Boolean, default=False, server_default=false())           # VIP given free by a promotion
    vip_checked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)   # for freezing the VIP clock
    viewers_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)    # «кто видел анкету»
    bumped_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)   # «Поднять»: on top of the feed until
    super_credits: Mapped[int] = mapped_column(Integer, default=0, server_default="0")                # unused «супер-симпатии»
    vip_notified: Mapped[str] = mapped_column(String(4), default="", server_default="")              # reminders sent: "", "3d", "end"
    # pending, published, rejected
    status: Mapped[str] = mapped_column(String(16), default="pending", index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class DatingLike(Base):
    __tablename__ = "dating_likes"
    __table_args__ = (UniqueConstraint("from_tg", "to_tg", name="uq_dating_like"),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    from_tg: Mapped[str] = mapped_column(String(40), index=True)
    to_tg: Mapped[str] = mapped_column(String(40), index=True)
    created_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=_now, nullable=True)
    notif_id: Mapped[int | None] = mapped_column(Integer, nullable=True)          # the notice to the other person (taken back on cancel)
    super: Mapped[bool] = mapped_column(Boolean, default=False, server_default=false())   # «супер-симпатия»: shown first


class Message(Base):
    __tablename__ = "messages"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    conversation_id: Mapped[int] = mapped_column(ForeignKey("conversations.id", ondelete="CASCADE"), index=True)
    sender_tg_id: Mapped[str] = mapped_column(String(40))
    text: Mapped[str] = mapped_column(Text)
    photo_key: Mapped[str | None] = mapped_column(String(40), nullable=True)
    file_key: Mapped[str | None] = mapped_column(String(40), nullable=True)      # PDF or voice message
    file_name: Mapped[str] = mapped_column(String(80), default="", server_default="")
    file_kind: Mapped[str] = mapped_column(String(8), default="", server_default="")   # pdf | audio
    read_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, index=True)


class Pricing(Base):
    """Paid services (ТЗ № 2). Prices are editable by administrators."""
    __tablename__ = "pricing"
    code: Mapped[str] = mapped_column(String(40), primary_key=True)  # e.g. job_vip
    category: Mapped[str] = mapped_column(String(30))
    kind: Mapped[str] = mapped_column(String(16))  # bump, vip, pin, highlight; for dating also invisible, viewers, super
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
    reminded: Mapped[bool] = mapped_column(Boolean, default=False)  # "ends tomorrow" notice sent
    promo: Mapped[bool] = mapped_column(Boolean, default=False, server_default=false())   # given free by a promotion: no lift in ranking
    frozen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)   # the clock is stopped since this moment


class Payment(Base):
    __tablename__ = "payments"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_tg_id: Mapped[str] = mapped_column(String(40), index=True)
    listing_id: Mapped[int | None] = mapped_column(ForeignKey("listings.id", ondelete="CASCADE"), index=True, nullable=True)
    profile_id: Mapped[int | None] = mapped_column(Integer, index=True, nullable=True)       # a dating profile instead of a listing
    note: Mapped[str] = mapped_column(String(200), default="", server_default="")             # «подарок», «компенсация», comments
    balance_used: Mapped[int] = mapped_column(Integer, default=0, server_default="0")        # stars taken from the person's balance
    company_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)       # paid for a company listing: the COMPANY balance is used
    service_code: Mapped[str] = mapped_column(String(40))
    stars: Mapped[int] = mapped_column(Integer)
    payload: Mapped[str] = mapped_column(String(64), unique=True)
    telegram_payment_charge_id: Mapped[str | None] = mapped_column(String(128), unique=True, nullable=True)
    # pending, paid, refunded, failed
    status: Mapped[str] = mapped_column(String(16), default="pending", index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    paid_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    promo_code: Mapped[str] = mapped_column(String(24), default="")
    list_stars: Mapped[int | None] = mapped_column(Integer, nullable=True)  # price before the discount


class PromoCode(Base):
    """A discount code for paid services: a percent or a fixed number of stars off."""
    __tablename__ = "promo_codes"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    code: Mapped[str] = mapped_column(String(24), unique=True)
    percent: Mapped[int | None] = mapped_column(Integer, nullable=True)
    fixed_stars: Mapped[int | None] = mapped_column(Integer, nullable=True)
    max_uses: Mapped[int | None] = mapped_column(Integer, nullable=True)
    used_count: Mapped[int] = mapped_column(Integer, default=0)
    valid_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class Group(Base):
    __tablename__ = "groups"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(120))
    flag: Mapped[str] = mapped_column(String(8), default="")
    telegram_link: Mapped[str] = mapped_column(String(255), default="")
    description: Mapped[str] = mapped_column(Text, default="")
    members_count: Mapped[int] = mapped_column(Integer, default=0)
    kind: Mapped[str] = mapped_column(String(10), default="nation")  # nation | city


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
    created_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=_now, nullable=True)


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


class Region(Base):
    """One of the 17 regions of South Korea (see app/geo.py)."""
    __tablename__ = "regions"
    code: Mapped[str] = mapped_column(String(20), primary_key=True)
    name_ru: Mapped[str] = mapped_column(String(60))
    name_ko: Mapped[str] = mapped_column(String(40))
    sort: Mapped[int] = mapped_column(Integer, default=0)


class District(Base):
    """A city (si), county (gun) or district (gu) inside a region."""
    __tablename__ = "districts"
    __table_args__ = (UniqueConstraint("region_code", "name_ko", name="uq_district"),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    region_code: Mapped[str] = mapped_column(ForeignKey("regions.code"), index=True)
    name_ru: Mapped[str] = mapped_column(String(60))
    name_ko: Mapped[str] = mapped_column(String(40))
    kind: Mapped[str] = mapped_column(String(4))  # gu, si, gun
    label: Mapped[str] = mapped_column(String(100), index=True)
    sort: Mapped[int] = mapped_column(Integer, default=0)


class ListingPhoto(Base):
    """A photo of a listing. Files are processed JPEGs named by an unguessable key (see app/photos.py)."""
    __tablename__ = "listing_photos"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    listing_id: Mapped[int] = mapped_column(ForeignKey("listings.id", ondelete="CASCADE"), index=True)
    key: Mapped[str] = mapped_column(String(40), unique=True)
    position: Mapped[int] = mapped_column(Integer, default=0)
    # live: shown; pending: added to a published listing, waits for a moderator; removal: asked to delete
    status: Mapped[str] = mapped_column(String(10), default="live", index=True)
    sha: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)  # fingerprint of the stored picture
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class Application(Base):
    """A reply to a vacancy. The author sees the contact the applicant chose to give, never the Telegram ID."""
    __tablename__ = "applications"
    __table_args__ = (UniqueConstraint("listing_id", "applicant_tg_id", name="uq_application_once"),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    listing_id: Mapped[int] = mapped_column(ForeignKey("listings.id"), index=True)
    applicant_tg_id: Mapped[str] = mapped_column(String(40), index=True)
    message: Mapped[str] = mapped_column(String(1000), default="")
    contact: Mapped[str] = mapped_column(String(160), default="")
    visa: Mapped[str] = mapped_column(String(8), default="")
    city: Mapped[str] = mapped_column(String(100), default="")
    status: Mapped[str] = mapped_column(String(12), default="new")  # new, viewed, invited, rejected, withdrawn
    name: Mapped[str] = mapped_column(String(40), default="")  # display name of the applicant at that moment
    reply: Mapped[str] = mapped_column(String(500), default="")  # the employer's answer to the applicant
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class SavedSearch(Base):
    """A user's saved filters; the bot messages them when new matching listings are published."""
    __tablename__ = "saved_searches"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_tg_id: Mapped[str] = mapped_column(String(40), index=True)
    name: Mapped[str] = mapped_column(String(80), default="")
    params: Mapped[dict] = mapped_column(JSON, default=dict)
    last_checked_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    paused: Mapped[bool] = mapped_column(Boolean, default=False, server_default=false())   # no alerts while paused
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class Notification(Base):
    """In-app feed: every notice is stored here, and also sent to Telegram when the user allows that kind."""
    __tablename__ = "notifications"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_tg_id: Mapped[str] = mapped_column(String(40), index=True)
    kind: Mapped[str] = mapped_column(String(16), default="system")
    text: Mapped[str] = mapped_column(String(600))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, index=True)
    read_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    tg_pending: Mapped[bool] = mapped_column(Boolean, default=False, server_default=false(), index=True)   # held back for Telegram (quiet hours, digest)


class ArticleBookmark(Base):
    __tablename__ = "article_bookmarks"
    __table_args__ = (UniqueConstraint("user_tg_id", "article_id", name="uq_article_bookmark"),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_tg_id: Mapped[str] = mapped_column(String(40), index=True)
    article_id: Mapped[int] = mapped_column(ForeignKey("articles.id", ondelete="CASCADE"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class Broadcast(Base):
    """A message to many users. Created as a draft, confirmed by an administrator, then sent slowly by the bot."""
    __tablename__ = "broadcasts"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    created_by: Mapped[str] = mapped_column(String(40))
    text: Mapped[str] = mapped_column(String(1000))
    region_code: Mapped[str | None] = mapped_column(String(20), nullable=True)  # only people from this region
    status: Mapped[str] = mapped_column(String(12), default="draft", index=True)  # draft, scheduled, sending, done, cancelled
    audience_kind: Mapped[str] = mapped_column(String(12), default="all", server_default="all")   # all, employers, authors, active30, inactive30
    send_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    total: Mapped[int] = mapped_column(Integer, default=0)
    sent: Mapped[int] = mapped_column(Integer, default=0)
    failed: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class BroadcastTarget(Base):
    __tablename__ = "broadcast_targets"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    broadcast_id: Mapped[int] = mapped_column(ForeignKey("broadcasts.id", ondelete="CASCADE"), index=True)
    tg_id: Mapped[str] = mapped_column(String(40))
    status: Mapped[str] = mapped_column(String(8), default="pending", index=True)  # pending, sent, failed


class Banner(Base):
    """A strip on the home screen (news, promotion). Shown while active and inside its dates."""
    __tablename__ = "banners"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    text: Mapped[str] = mapped_column(String(160))
    link: Mapped[str] = mapped_column(String(255), default="")
    starts_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    ends_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class Heartbeat(Base):
    """"I am alive" marks written by background processes (the bot), read by /health."""
    __tablename__ = "heartbeats"
    name: Mapped[str] = mapped_column(String(30), primary_key=True)
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class RateHit(Base):
    """One counted action for the shared rate limit (all web workers see the same rows)."""
    __tablename__ = "rate_hits"
    id: Mapped[int] = mapped_column(primary_key=True)
    bucket: Mapped[str] = mapped_column(String(120), index=True)
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, index=True)


class ReferralSource(Base):
    """Where a person first came from: the tag in t.me/<bot>?start=src_<tag> (first touch only)."""
    __tablename__ = "referral_sources"
    tg_id: Mapped[str] = mapped_column(String(40), primary_key=True)
    source: Mapped[str] = mapped_column(String(40), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class Review(Base):
    """A rating of a listing's author, written by someone who really talked to them about that listing."""
    __tablename__ = "reviews"
    __table_args__ = (UniqueConstraint("reviewer_tg_id", "listing_id", name="uq_review"),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    listing_id: Mapped[int] = mapped_column(Integer, index=True)
    target_tg_id: Mapped[str] = mapped_column(String(40), index=True)
    reviewer_tg_id: Mapped[str] = mapped_column(String(40), index=True)
    rating: Mapped[int] = mapped_column(Integer)
    text: Mapped[str] = mapped_column(String(500), default="")
    tags: Mapped[str] = mapped_column(String(80), default="", server_default="")      # quick labels, comma separated
    reply: Mapped[str] = mapped_column(String(500), default="", server_default="")   # the author's answer, shown under the review
    status: Mapped[str] = mapped_column(String(12), default="published", index=True)  # published | hidden
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class Resume(Base):
    """«Ищу работу»: a job seeker's card. Public only after moderation; contacts are never shown,
    an employer reaches the person through an invitation to a chat about the employer's own vacancy."""
    __tablename__ = "resumes"
    owner_tg_id: Mapped[str] = mapped_column(String(40), primary_key=True)
    position: Mapped[str] = mapped_column(String(80))
    city: Mapped[str] = mapped_column(String(100), index=True)
    region_code: Mapped[str | None] = mapped_column(String(20), nullable=True, index=True)
    visa: Mapped[str] = mapped_column(String(8), default="")
    experience: Mapped[str] = mapped_column(String(300), default="")
    about: Mapped[str] = mapped_column(String(700), default="")
    salary_from: Mapped[int | None] = mapped_column(Integer, nullable=True)   # ₩ per month
    status: Mapped[str] = mapped_column(String(12), default="pending", index=True)  # pending | approved | rejected | hidden
    note: Mapped[str] = mapped_column(String(300), default="")
    search_text: Mapped[str] = mapped_column(String(1200), default="")   # lower-cased in Python: SQL lower() skips Cyrillic on SQLite
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class BannedPhoto(Base):
    """Fingerprint (difference hash) of a picture a moderator banned. Near-identical uploads are refused."""
    __tablename__ = "banned_photos"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    dhash: Mapped[str] = mapped_column(String(16), index=True)
    note: Mapped[str] = mapped_column(String(200), default="")
    created_by: Mapped[str] = mapped_column(String(40), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class Referral(Base):
    """Who invited whom. Only a person who was not on the service yet can be counted, once."""
    __tablename__ = "referrals"
    invited_tg_id: Mapped[str] = mapped_column(String(40), primary_key=True)
    inviter_tg_id: Mapped[str] = mapped_column(String(40), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class Setting(Base):
    """Texts and switches the owner edits in the admin panel (no server restart, no .env)."""
    __tablename__ = "app_settings"
    key: Mapped[str] = mapped_column(String(40), primary_key=True)
    value: Mapped[str] = mapped_column(Text, default="")


class Faq(Base):
    __tablename__ = "faq"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    question: Mapped[str] = mapped_column(String(200))
    answer: Mapped[str] = mapped_column(Text)
    position: Mapped[int] = mapped_column(Integer, default=0)
    active: Mapped[bool] = mapped_column(Boolean, default=True)


class Ticket(Base):
    """A request to support or an appeal against a moderator's decision. Answered inside the app, no nick needed."""
    __tablename__ = "tickets"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_tg_id: Mapped[str] = mapped_column(String(40), index=True)
    kind: Mapped[str] = mapped_column(String(10), default="support")      # support | appeal
    listing_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    status: Mapped[str] = mapped_column(String(10), default="open", index=True)   # open | answered | closed
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, index=True)


class TicketMessage(Base):
    __tablename__ = "ticket_messages"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    ticket_id: Mapped[int] = mapped_column(ForeignKey("tickets.id", ondelete="CASCADE"), index=True)
    staff: Mapped[bool] = mapped_column(Boolean, default=False)
    author: Mapped[str] = mapped_column(String(40), default="")
    text: Mapped[str] = mapped_column(String(1500))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class TypingMark(Base):
    """«Печатает…»: the last moment a person typed in a chat (only the last few seconds matter)."""
    __tablename__ = "typing_marks"
    conv_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_tg_id: Mapped[str] = mapped_column(String(40), primary_key=True)
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class WizardDraft(Base):
    """The unfinished «new listing» form, kept on the server so it survives a new phone or cleared storage."""
    __tablename__ = "wizard_drafts"
    user_tg_id: Mapped[str] = mapped_column(String(40), primary_key=True)
    data: Mapped[dict] = mapped_column(JSON, default=dict)
    step: Mapped[int] = mapped_column(Integer, default=0)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class ClientError(Base):
    """What went wrong on a person's phone: the app reports it itself, so nobody has to send screenshots."""
    __tablename__ = "client_errors"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_tg_id: Mapped[str] = mapped_column(String(40), default="", index=True)
    where: Mapped[str] = mapped_column(String(120), default="")
    status: Mapped[int] = mapped_column(Integer, default=0)
    message: Mapped[str] = mapped_column(String(300), default="")
    request_id: Mapped[str] = mapped_column(String(40), default="")
    agent: Mapped[str] = mapped_column(String(160), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, index=True)


class UserNote(Base):
    """A private note about a person, visible to staff only."""
    __tablename__ = "user_notes"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_tg_id: Mapped[str] = mapped_column(String(40), index=True)
    author: Mapped[str] = mapped_column(String(40))
    text: Mapped[str] = mapped_column(String(500))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class Claim(Base):
    """«Взято в работу»: a soft lock so two staff members do not handle the same item at once."""
    __tablename__ = "claims"
    kind: Mapped[str] = mapped_column(String(12), primary_key=True)
    obj_id: Mapped[str] = mapped_column(String(40), primary_key=True)
    staff: Mapped[str] = mapped_column(String(40))
    until: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class ConvHide(Base):
    """«Скрыть чат»: the chat disappears from the list of one person, and comes back when a new message arrives."""
    __tablename__ = "conv_hides"
    conv_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_tg_id: Mapped[str] = mapped_column(String(40), primary_key=True)
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class DatingView(Base):
    """Who opened a profile (shown to the owner only with the paid service «кто видел анкету»)."""
    __tablename__ = "dating_views"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    profile_id: Mapped[int] = mapped_column(Integer, index=True)
    viewer_tg: Mapped[str] = mapped_column(String(40), index=True)
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class DatingPrivatePhoto(Base):
    """A private photo: seen by VIP viewers and by people who like each other; moderators see all. Never in lists."""
    __tablename__ = "dating_private_photos"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    profile_id: Mapped[int] = mapped_column(Integer, index=True)
    key: Mapped[str] = mapped_column(String(80))
    status: Mapped[str] = mapped_column(String(10), default="pending", index=True)   # pending | live | rejected
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
