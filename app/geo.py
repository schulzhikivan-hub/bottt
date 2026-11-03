"""South Korea only: 17 regions and every city, county and district below them (229 in total).

Every listing, profile and dating profile points to a place from this list, so filters work and the
site stays about Korea. Names are given in Russian and in Korean (Hangul) for taxis and maps.
The Russian spelling is a common transliteration; the Hangul is the exact administrative name.
"""
from dataclasses import dataclass

from fastapi import APIRouter, Depends, Response
from sqlalchemy import select, func
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import Region, District

router = APIRouter()

# (code, Russian, Korean)
REGIONS = [
    ("seoul", "Сеул", "서울특별시"), ("busan", "Пусан", "부산광역시"), ("daegu", "Тэгу", "대구광역시"),
    ("incheon", "Инчхон", "인천광역시"), ("gwangju", "Кванджу", "광주광역시"), ("daejeon", "Тэджон", "대전광역시"),
    ("ulsan", "Ульсан", "울산광역시"), ("sejong", "Седжон", "세종특별자치시"), ("gyeonggi", "Кёнгидо", "경기도"),
    ("gangwon", "Канвондо", "강원특별자치도"), ("chungbuk", "Чхунчхон-Пукто", "충청북도"),
    ("chungnam", "Чхунчхон-Намдо", "충청남도"), ("jeonbuk", "Чолла-Пукто", "전북특별자치도"),
    ("jeonnam", "Чолла-Намдо", "전라남도"), ("gyeongbuk", "Кёнсан-Пукто", "경상북도"),
    ("gyeongnam", "Кёнсан-Намдо", "경상남도"), ("jeju", "Чеджу", "제주특별자치도"),
]

# region code -> [(Russian, Korean, kind)]; kind: gu (district), si (city), gun (county)
_G, _S, _N = "gu", "si", "gun"
DISTRICTS = {
    "seoul": [(r, k, _G) for r, k in [
        ("Чонно", "종로구"), ("Чун", "중구"), ("Ёнсан", "용산구"), ("Сондон", "성동구"), ("Кванджин", "광진구"),
        ("Тондэмун", "동대문구"), ("Чунран", "중랑구"), ("Сонбук", "성북구"), ("Канбук", "강북구"),
        ("Тобон", "도봉구"), ("Нове", "노원구"), ("Ынпхён", "은평구"), ("Содэмун", "서대문구"), ("Мапхо", "마포구"),
        ("Янчхон", "양천구"), ("Кансо", "강서구"), ("Куро", "구로구"), ("Кымчхон", "금천구"),
        ("Ёндынпхо", "영등포구"), ("Тонджак", "동작구"), ("Кванак", "관악구"), ("Сочхо", "서초구"),
        ("Каннам", "강남구"), ("Сонпха", "송파구"), ("Кандон", "강동구")]],
    "busan": [(r, k, _G) for r, k in [
        ("Чун", "중구"), ("Со", "서구"), ("Тон", "동구"), ("Ёндо", "영도구"), ("Пусанджин", "부산진구"),
        ("Тонне", "동래구"), ("Нам", "남구"), ("Пук", "북구"), ("Хэундэ", "해운대구"), ("Саха", "사하구"),
        ("Кымджон", "금정구"), ("Кансо", "강서구"), ("Ёнджэ", "연제구"), ("Суён", "수영구"),
        ("Сасан", "사상구")]] + [("Кичжан", "기장군", _N)],
    "daegu": [(r, k, _G) for r, k in [
        ("Чун", "중구"), ("Тон", "동구"), ("Со", "서구"), ("Нам", "남구"), ("Пук", "북구"),
        ("Сусон", "수성구"), ("Тальсо", "달서구")]] + [("Тальсон", "달성군", _N), ("Кунви", "군위군", _N)],
    "incheon": [(r, k, _G) for r, k in [
        ("Чун", "중구"), ("Тон", "동구"), ("Мичухоль", "미추홀구"), ("Ёнсу", "연수구"), ("Намдон", "남동구"),
        ("Пупхён", "부평구"), ("Кеян", "계양구"), ("Со", "서구")]] + [("Канхва", "강화군", _N), ("Онджин", "옹진군", _N)],
    "gwangju": [(r, k, _G) for r, k in [
        ("Тон", "동구"), ("Со", "서구"), ("Нам", "남구"), ("Пук", "북구"), ("Кванъсан", "광산구")]],
    "daejeon": [(r, k, _G) for r, k in [
        ("Тон", "동구"), ("Чун", "중구"), ("Со", "서구"), ("Юсон", "유성구"), ("Тэдок", "대덕구")]],
    "ulsan": [(r, k, _G) for r, k in [
        ("Чун", "중구"), ("Нам", "남구"), ("Тон", "동구"), ("Пук", "북구")]] + [("Ульджу", "울주군", _N)],
    "sejong": [("Седжон", "세종시", _S)],
    "gyeonggi": [(r, k, _S) for r, k in [
        ("Сувон", "수원시"), ("Соннам", "성남시"), ("Ыйджонбу", "의정부시"), ("Анян", "안양시"), ("Пучхон", "부천시"),
        ("Кванмён", "광명시"), ("Пхёнтхэк", "평택시"), ("Тондучхон", "동두천시"), ("Ансан", "안산시"),
        ("Коян", "고양시"), ("Квачхон", "과천시"), ("Кури", "구리시"), ("Намянджу", "남양주시"), ("Осан", "오산시"),
        ("Сихын", "시흥시"), ("Кунпхо", "군포시"), ("Ыван", "의왕시"), ("Ханам", "하남시"), ("Ёнъин", "용인시"),
        ("Пхаджу", "파주시"), ("Ичхон", "이천시"), ("Ансон", "안성시"), ("Кимпхо", "김포시"), ("Хвасон", "화성시"),
        ("Кванджу", "광주시"), ("Янджу", "양주시"), ("Пхочхон", "포천시"), ("Ёджу", "여주시")]] +
        [("Ёнчхон", "연천군", _N), ("Капхён", "가평군", _N), ("Янпхён", "양평군", _N)],
    "gangwon": [(r, k, _S) for r, k in [
        ("Чхунчхон", "춘천시"), ("Вонджу", "원주시"), ("Каннын", "강릉시"), ("Тонхэ", "동해시"),
        ("Тхэбэк", "태백시"), ("Сокчхо", "속초시"), ("Самчхок", "삼척시")]] + [(r, k, _N) for r, k in [
        ("Хончхон", "홍천군"), ("Хвенсон", "횡성군"), ("Ёнволь", "영월군"), ("Пхёнчхан", "평창군"),
        ("Чонсон", "정선군"), ("Чхорвон", "철원군"), ("Хвачхон", "화천군"), ("Янгу", "양구군"),
        ("Инджэ", "인제군"), ("Косон", "고성군"), ("Янъян", "양양군")]],
    "chungbuk": [(r, k, _S) for r, k in [("Чхонджу", "청주시"), ("Чхунджу", "충주시"), ("Чечхон", "제천시")]] +
        [(r, k, _N) for r, k in [
        ("Поын", "보은군"), ("Окчхон", "옥천군"), ("Ёндон", "영동군"), ("Чхынпхён", "증평군"),
        ("Чинчхон", "진천군"), ("Кесан", "괴산군"), ("Ымсон", "음성군"), ("Танъян", "단양군")]],
    "chungnam": [(r, k, _S) for r, k in [
        ("Чхонан", "천안시"), ("Конджу", "공주시"), ("Порён", "보령시"), ("Асан", "아산시"), ("Сосан", "서산시"),
        ("Нонсан", "논산시"), ("Керён", "계룡시"), ("Танджин", "당진시")]] + [(r, k, _N) for r, k in [
        ("Кымсан", "금산군"), ("Пуё", "부여군"), ("Сочхон", "서천군"), ("Чхонъян", "청양군"),
        ("Хонсон", "홍성군"), ("Йесан", "예산군"), ("Тхэан", "태안군")]],
    "jeonbuk": [(r, k, _S) for r, k in [
        ("Чонджу", "전주시"), ("Кунсан", "군산시"), ("Иксан", "익산시"), ("Чонып", "정읍시"),
        ("Намвон", "남원시"), ("Кимдже", "김제시")]] + [(r, k, _N) for r, k in [
        ("Ванджу", "완주군"), ("Чинан", "진안군"), ("Муджу", "무주군"), ("Чансу", "장수군"),
        ("Имсиль", "임실군"), ("Сунчхан", "순창군"), ("Кочхан", "고창군"), ("Пуан", "부안군")]],
    "jeonnam": [(r, k, _S) for r, k in [
        ("Мокпхо", "목포시"), ("Ёсу", "여수시"), ("Сунчхон", "순천시"), ("Наджу", "나주시"),
        ("Кванъян", "광양시")]] + [(r, k, _N) for r, k in [
        ("Тамъян", "담양군"), ("Коксон", "곡성군"), ("Куре", "구례군"), ("Кохын", "고흥군"), ("Посон", "보성군"),
        ("Хвасун", "화순군"), ("Чанхын", "장흥군"), ("Канджин", "강진군"), ("Хэнам", "해남군"),
        ("Ёнам", "영암군"), ("Муан", "무안군"), ("Хампхён", "함평군"), ("Ёнгван", "영광군"),
        ("Чансон", "장성군"), ("Вандо", "완도군"), ("Чиндо", "진도군"), ("Синан", "신안군")]],
    "gyeongbuk": [(r, k, _S) for r, k in [
        ("Пхохан", "포항시"), ("Кёнджу", "경주시"), ("Кимчхон", "김천시"), ("Андон", "안동시"), ("Куми", "구미시"),
        ("Ёнджу", "영주시"), ("Ёнчхон", "영천시"), ("Санджу", "상주시"), ("Мунгён", "문경시"),
        ("Кёнсан", "경산시")]] + [(r, k, _N) for r, k in [
        ("Ысон", "의성군"), ("Чхонсон", "청송군"), ("Ёнъян", "영양군"), ("Ёндок", "영덕군"), ("Чхондо", "청도군"),
        ("Корён", "고령군"), ("Сонджу", "성주군"), ("Чхилькок", "칠곡군"), ("Йечхон", "예천군"),
        ("Понхва", "봉화군"), ("Ульджин", "울진군"), ("Уллын", "울릉군")]],
    "gyeongnam": [(r, k, _S) for r, k in [
        ("Чхангвон", "창원시"), ("Чинджу", "진주시"), ("Тхонён", "통영시"), ("Сачхон", "사천시"),
        ("Кимхэ", "김해시"), ("Мирян", "밀양시"), ("Кодже", "거제시"), ("Янсан", "양산시")]] + [(r, k, _N) for r, k in [
        ("Ырён", "의령군"), ("Хаман", "함안군"), ("Чханнён", "창녕군"), ("Косон", "고성군"), ("Намхэ", "남해군"),
        ("Хадон", "하동군"), ("Санчхон", "산청군"), ("Хамъян", "함양군"), ("Кочхан", "거창군"),
        ("Хапчхон", "합천군")]],
    "jeju": [("Чеджу", "제주시", _S), ("Согвипхо", "서귀포시", _S)],
}


def _norm(text: str) -> str:
    return " ".join((text or "").casefold().replace("ё", "е").split())


def district_label(region_ru: str, name_ru: str, kind: str, ambiguous: bool) -> str:
    """Text shown on cards. Metropolitan districts carry their city; clashing names carry the region."""
    if kind == "gu":
        return f"{region_ru}, {name_ru}"
    return f"{name_ru} ({region_ru})" if ambiguous else name_ru


def ensure_geo(db: Session) -> None:
    """Fill the reference tables on an empty database. Safe to run again."""
    if db.scalar(select(func.count()).select_from(Region)):
        return
    region_names = {r[1] for r in REGIONS}
    counts: dict[str, int] = {}
    for items in DISTRICTS.values():
        for ru, _ko, kind in items:
            if kind != "gu":
                counts[ru] = counts.get(ru, 0) + 1
    for order, (code, ru, ko) in enumerate(REGIONS):
        db.add(Region(code=code, name_ru=ru, name_ko=ko, sort=order))
    db.flush()
    sort = 0
    for code, ru_region, _ko in REGIONS:
        for ru, ko, kind in DISTRICTS[code]:
            # A name clashes if it exists twice, or equals ANOTHER region (Gyeonggi's Кванджу vs the city Кванджу)
            ambiguous = kind != "gu" and (counts.get(ru, 0) > 1 or (ru in region_names and ru != ru_region))
            db.add(District(region_code=code, name_ru=ru, name_ko=ko, kind=kind, sort=sort,
                            label=district_label(ru_region, ru, kind, ambiguous)))
            sort += 1
    db.commit()


@dataclass
class Place:
    region_code: str
    district_id: int | None
    label: str


def resolve_place(db: Session, district_id: int | None = None, region_code: str | None = None,
                  text: str | None = None) -> Place | None:
    """Turn what the client sent into a place in Korea, or None.

    A district wins over a region; free text is accepted only when it names exactly one place
    (for example "Асан", "아산시" or "Сеул, Каннам"). Anything else (Moscow, "somewhere") returns None.
    """
    if district_id is not None:
        d = db.get(District, district_id)
        if d is None:
            return None
        if region_code and region_code != d.region_code:
            return None
        return Place(d.region_code, d.id, d.label)
    if region_code:
        r = db.get(Region, region_code)
        return Place(r.code, None, r.name_ru) if r else None
    wanted = _norm(text or "")
    if not wanted:
        return None
    matches: list[Place] = []
    for r in db.scalars(select(Region)).all():
        if wanted in (_norm(r.name_ru), _norm(r.name_ko)):
            matches.append(Place(r.code, None, r.name_ru))
    for d in db.scalars(select(District)).all():
        if wanted in (_norm(d.name_ru), _norm(d.name_ko), _norm(d.label)):
            matches.append(Place(d.region_code, d.id, d.label))
    # "Седжон" names both the region and its only city: the city is the more exact answer
    district_labels = {(m.region_code, _norm(m.label)) for m in matches if m.district_id is not None}
    matches = [m for m in matches if m.district_id is not None or (m.region_code, _norm(m.label)) not in district_labels]
    return matches[0] if len(matches) == 1 else None


def backfill_places(db: Session) -> None:
    """Attach places to listings created with free text before this list existed (best effort)."""
    from app.models import Listing
    changed = False
    for item in db.scalars(select(Listing).where(Listing.region_code.is_(None))).all():
        place = resolve_place(db, text=item.city)
        if place:
            item.region_code, item.district_id, item.city = place.region_code, place.district_id, place.label
            changed = True
    if changed:
        db.commit()


@router.get("/api/geo")
def geo(response: Response, db: Session = Depends(get_db)):
    """The full list for the pickers: regions with their districts."""
    response.headers["Cache-Control"] = "public, max-age=3600"
    districts: dict[str, list] = {}
    for d in db.scalars(select(District).order_by(District.sort)).all():
        districts.setdefault(d.region_code, []).append(
            {"id": d.id, "name": d.name_ru, "name_ko": d.name_ko, "label": d.label, "kind": d.kind})
    return {"regions": [{"code": r.code, "name": r.name_ru, "name_ko": r.name_ko,
                         "districts": districts.get(r.code, [])}
                        for r in db.scalars(select(Region).order_by(Region.sort)).all()]}
