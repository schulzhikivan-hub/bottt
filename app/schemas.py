from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field, ConfigDict, field_validator

from app.fields import (VisaCode, Employment, Schedule, SalaryPeriod, HousingType, GoodsCategory,
                        visas_from_text)

CATEGORIES = {"job", "housing", "marketplace"}
MAX_ACTIVE_PER_CATEGORY = 5


class ListingCreate(BaseModel):
    category: str
    title: str = Field(min_length=3, max_length=160)
    # Place in South Korea: pick a district (best) or a region from the list; free text is accepted only
    # when it names exactly one place of the list (see app/geo.py)
    city: str | None = Field(default=None, max_length=100)
    region_code: str | None = Field(default=None, max_length=20)
    district_id: int | None = None
    price: str = Field(default="", max_length=80)
    description: str = Field(default="", max_length=4000)
    contact: str = Field(default="", max_length=160)
    # Searchable details; only the ones that fit the category are kept (see catalog.py)
    price_amount: int | None = Field(default=None, ge=0, le=10_000_000_000)
    housing_provided: bool | None = None
    meals_provided: bool | None = None
    rooms: int | None = Field(default=None, ge=0, le=20)
    condition: Literal["new", "used"] | None = None
    salary_period: SalaryPeriod | None = None
    visas: list[VisaCode] | None = Field(default=None, max_length=11)
    employment: Employment | None = None
    schedule: Schedule | None = None
    housing_type: HousingType | None = None
    deposit: int | None = Field(default=None, ge=0, le=10_000_000_000)
    area_m2: int | None = Field(default=None, ge=1, le=2000)
    pets_allowed: bool | None = None
    furnished: bool | None = None
    goods_category: GoodsCategory | None = None
    negotiable: bool | None = None
    details: dict | None = None  # descriptive details, validated per section (see app/fields.py)
    draft: bool = False  # save without sending to moderation; "submit" sends it


class ListingOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    category: str
    title: str
    city: str
    price: str
    description: str
    contact: str
    status: str
    note: str = ""
    region_code: str | None = None
    district_id: int | None = None
    price_amount: int | None = None
    housing_provided: bool | None = None
    meals_provided: bool | None = None
    rooms: int | None = None
    condition: str | None = None
    salary_period: str | None = None
    visas: list[str] = []
    employment: str | None = None
    schedule: str | None = None
    housing_type: str | None = None
    deposit: int | None = None
    area_m2: int | None = None
    pets_allowed: bool | None = None
    furnished: bool | None = None
    goods_category: str | None = None
    negotiable: bool | None = None
    details: dict = {}
    photos: list[dict] = []  # {id, url, thumb, status}; the first one is the cover
    cover: str | None = None
    views: int = 0
    reserved: bool = False
    placements: list[dict] = []  # owner only: active paid services {kind, ends_at}
    has_edit: bool = False  # owner only: changes wait for a moderator
    published_at: datetime | None = None
    expires_at: datetime | None = None  # owner only
    author: dict = {}  # public trust signals on the listing page
    applications: dict | None = None  # owner only: {total, new}
    promo: str | None = None  # active paid placement: pin, vip, bump, highlight
    created_at: datetime | None = None


    @field_validator("visas", mode="before")
    @classmethod
    def _visas(cls, value):
        return visas_from_text(value) if isinstance(value, str) or value is None else value

    @field_validator("details", mode="before")
    @classmethod
    def _details(cls, value):
        return value or {}


class MessageIn(BaseModel):
    text: str = Field(min_length=1, max_length=2000)


class MessageOut(BaseModel):
    id: int
    mine: bool
    text: str
    created_at: datetime
    read: bool = False          # my message that the other person has opened
    photo: str | None = None    # picture sent in the chat
    file: dict | None = None    # {"name","kind","url"}: PDF or voice message


class ConversationOut(BaseModel):
    id: int
    listing_id: int | None
    listing_title: str
    last_text: str
    last_at: datetime | None
    unread: int = 0
    kind: str = "listing"             # listing | match
    role: str = "buyer"               # buyer: I wrote to the author; seller: someone wrote to me
    other_name: str = ""
    other_initial: str = ""
    listing_cover: str | None = None
    listing_price: str = ""
    listing_category: str = ""
    listing_status: str = ""
    last_mine: bool = False
