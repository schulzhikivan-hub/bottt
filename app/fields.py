"""What a listing can say about itself, section by section.

Searchable values (visa, schedule, housing type…) are stored in columns and used by filters.
Descriptive values (company, floor, station…) live in `details`. The same lists, with Russian
labels, are sent to the Mini App by GET /api/fields, so the forms and the server never disagree.
"""
from typing import Literal

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, ConfigDict, Field, ValidationError

router = APIRouter()
DATE = r"^\d{4}-\d{2}-\d{2}$"

VisaCode = Literal["E-9", "E-7", "F-2", "F-4", "F-5", "F-6", "D-2", "D-4", "H-2", "C-4", "G-1"]
Employment = Literal["full", "part", "daily", "contract"]
Schedule = Literal["day", "night", "shift"]
SalaryPeriod = Literal["month", "day", "hour"]
HousingType = Literal["apartment", "studio", "room", "dorm", "officetel", "house"]
GoodsCategory = Literal["phones", "electronics", "furniture", "appliances", "clothes", "kids", "tools", "auto", "other"]

VISAS = ["E-9", "E-7", "F-2", "F-4", "F-5", "F-6", "D-2", "D-4", "H-2", "C-4", "G-1"]
LABELS = {
    "employment": [("full", "Полная"), ("part", "Частичная"),
                   ("daily", "Подённо, разово"), ("contract", "Контракт")],
    "schedule": [("day", "Дневная смена"), ("night", "Ночная смена"), ("shift", "Посменно")],
    "salary_period": [("month", "в месяц"), ("day", "в день"), ("hour", "в час")],
    "housing_type": [("apartment", "Квартира"), ("studio", "Студия (вонрум)"), ("room", "Комната"),
                     ("dorm", "Место в общежитии"), ("officetel", "Офистель"), ("house", "Дом")],
    "goods_category": [("phones", "Телефоны"), ("electronics", "Электроника"), ("furniture", "Мебель"),
                       ("appliances", "Бытовая техника"), ("clothes", "Одежда и обувь"), ("kids", "Детские товары"),
                       ("tools", "Инструменты"), ("auto", "Авто и мото"), ("other", "Разное")],
    "condition": [("new", "Новое"), ("used", "Б/у")],
}


class JobDetails(BaseModel):
    model_config = ConfigDict(extra="ignore")
    company: str | None = Field(default=None, max_length=120)
    position: str | None = Field(default=None, max_length=120)
    vacancies: int | None = Field(default=None, ge=1, le=999)
    start_date: str | None = Field(default=None, pattern=DATE)
    experience: bool | None = None
    transport: bool | None = None
    salary_max: int | None = Field(default=None, ge=0, le=10_000_000_000)


class HousingDetails(BaseModel):
    model_config = ConfigDict(extra="ignore")
    maintenance_fee: int | None = Field(default=None, ge=0, le=10_000_000_000)
    floor: int | None = Field(default=None, ge=-5, le=200)
    available_from: str | None = Field(default=None, pattern=DATE)
    contract_term: str | None = Field(default=None, max_length=40)
    station: str | None = Field(default=None, max_length=80)


class GoodsDetails(BaseModel):
    model_config = ConfigDict(extra="ignore")
    brand: str | None = Field(default=None, max_length=60)
    delivery: bool | None = None


DETAILS = {"job": JobDetails, "housing": HousingDetails, "marketplace": GoodsDetails}
# Searchable values that belong to one section only
CATEGORY_EXTRAS = {
    "job": {"housing_provided", "meals_provided", "salary_period", "visas", "employment", "schedule"},
    "housing": {"rooms", "housing_type", "deposit", "area_m2", "pets_allowed", "furnished"},
    "marketplace": {"condition", "goods_category", "negotiable"},
}
ALL_EXTRAS = set().union(*CATEGORY_EXTRAS.values())
SEARCHABLE_DETAILS = ("company", "position", "station", "brand")


def clean_details(category: str, raw: dict | None) -> dict | None:
    """Validate descriptive details for the section; unknown keys are dropped, empty ones removed."""
    model = DETAILS.get(category)
    if raw is None or model is None:
        return None
    try:
        parsed = model.model_validate(raw).model_dump(exclude_none=True)
    except ValidationError:
        raise HTTPException(422, "Проверьте поля «Подробности»: даты в формате ГГГГ-ММ-ДД, числа без букв")
    return {k: (v.strip() if isinstance(v, str) else v) for k, v in parsed.items() if v not in ("", None)} or None


def visas_to_text(visas: list[str] | None) -> str | None:
    """Stored as ',E-9,F-4,' so a filter can look for ',E-9,' without matching other codes."""
    codes = sorted(set(visas or []))
    return "," + ",".join(codes) + "," if codes else None


def visas_from_text(text: str | None) -> list[str]:
    return [v for v in (text or "").split(",") if v]


@router.get("/api/fields")
def fields():
    """Lists for the forms and filters: value and Russian label."""
    out = {key: [{"value": v, "label": label} for v, label in items] for key, items in LABELS.items()}
    out["visas"] = [{"value": v, "label": v} for v in VISAS]
    return out
