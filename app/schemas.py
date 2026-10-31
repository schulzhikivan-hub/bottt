from datetime import datetime
from pydantic import BaseModel, Field, ConfigDict

CATEGORIES = {"job", "housing", "marketplace"}
MAX_ACTIVE_PER_CATEGORY = 5


class ListingCreate(BaseModel):
    category: str
    title: str = Field(min_length=3, max_length=160)
    city: str = Field(min_length=2, max_length=80)
    price: str = Field(default="", max_length=80)
    description: str = Field(default="", max_length=4000)
    contact: str = Field(default="", max_length=160)


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


class MessageIn(BaseModel):
    text: str = Field(min_length=1, max_length=2000)


class MessageOut(BaseModel):
    id: int
    mine: bool
    text: str
    created_at: datetime


class ConversationOut(BaseModel):
    id: int
    listing_id: int | None
    listing_title: str
    last_text: str
    last_at: datetime | None
