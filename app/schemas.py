from pydantic import BaseModel, Field, ConfigDict

CATEGORIES = {"job", "housing", "marketplace"}

class ListingCreate(BaseModel):
    category: str
    title: str = Field(min_length=3, max_length=160)
    city: str = Field(min_length=2, max_length=80)
    price: str = Field(default="", max_length=80)
    description: str = Field(default="", max_length=4000)
    contact: str = Field(default="", max_length=160)
    author_telegram_id: str = Field(default="", max_length=40)

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
