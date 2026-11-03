"""Forgiving search: a word typed in the wrong keyboard layout, in Latin letters, with a different ending or with a typo
still finds the listing. Everything runs in Python so it behaves the same on SQLite and PostgreSQL."""
import difflib
import re

from sqlalchemy import select
from sqlalchemy.orm import Session

_EN = "qwertyuiop[]asdfghjkl;'zxcvbnm,.`"
_RU = "йцукенгшщзхъфывапролджэячсмитьбюё"
LAYOUT = {ord(a): b for a, b in zip(_EN, _RU)}

CITIES = {"seoul": "сеул", "busan": "пусан", "pusan": "пусан", "incheon": "инчхон", "daegu": "тэгу", "daejeon": "тэджон",
          "gwangju": "кванджу", "ulsan": "ульсан", "suwon": "сувон", "ansan": "ансан", "asan": "асан", "cheonan": "чхонан",
          "changwon": "чханвон", "jeju": "чеджу", "gimhae": "кимхэ", "hwaseong": "хвасон", "pyeongtaek": "пхёнтхэк",
          "siheung": "сихын", "bucheon": "пучхон", "gimpo": "кимпхо", "paju": "паджу", "yongin": "ёнин", "goyang": "коян"}
_DIGRAPHS = [("shch", "щ"), ("sch", "щ"), ("sh", "ш"), ("ch", "ч"), ("zh", "ж"), ("kh", "х"), ("ts", "ц"), ("ya", "я"),
             ("yu", "ю"), ("yo", "ё"), ("ye", "е"), ("ph", "ф")]
_SINGLE = dict(zip("abvgdezijklmnoprstufhcyqwx", "абвгдезийклмнопрстуфхцыквкс"))
_SINGLE.update({"j": "дж", "w": "в", "x": "кс", "q": "к", "y": "й"})


def translit(word: str) -> str:
    out, i = [], 0
    while i < len(word):
        for lat, ru in _DIGRAPHS:
            if word.startswith(lat, i):
                out.append(ru)
                i += len(lat)
                break
        else:
            out.append(_SINGLE.get(word[i], word[i]))
            i += 1
    return "".join(out)


def variants(word: str) -> list[str]:
    """The word itself plus plausible readings of it. The first item is always the original."""
    w = word.lower()
    seen = [w]

    def add(v):
        if v and v not in seen:
            seen.append(v)
    if re.fullmatch(r"[a-z]+", w):
        add(CITIES.get(w))
        add(translit(w))
    if re.fullmatch(r"[a-z\[\];',.`]+", w):
        add(w.translate(LAYOUT))                     # «ghjuhfvvbcn» typed on an English layout
    if re.fullmatch(r"[а-яё]{5,}", w):
        add(w[:-2] if len(w) >= 7 else w[:-1])        # «сварщика» → «сварщ»: endings do not matter
    return seen


def vocabulary(db: Session, limit: int = 1500) -> set[str]:
    from app.models import Listing
    words: set[str] = set()
    for (text,) in db.execute(select(Listing.search_text).where(Listing.status == "approved", Listing.search_text.is_not(None))
                              .order_by(Listing.id.desc()).limit(limit)).all():
        words.update(w for w in re.findall(r"[a-zа-яё0-9]{4,}", text or ""))
    return words


def correct(word: str, vocab: set[str]) -> str | None:
    """Closest known word for a typo (needs 4+ letters and a clear match)."""
    w = word.lower()
    if len(w) < 4 or w in vocab:
        return None
    best = difflib.get_close_matches(w, vocab, n=1, cutoff=0.8)
    return best[0] if best else None
