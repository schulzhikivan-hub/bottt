"""Initial reference data. Idempotent: runs only on empty tables."""
from sqlalchemy import select, func
from sqlalchemy.orm import Session

from app.models import Group, Article
from app.payments import ensure_pricing
from app.geo import ensure_geo

# Communities from screen 9 of the design. Telegram links are added by administrators.
GROUPS = [
    ("Русскоговорящие в Корее", "🇷🇺", "Русскоязычные"),
    ("Вьетнамцы в Корее", "🇻🇳", "Вьетнамцы"),
    ("Тайцы в Корее", "🇹🇭", "Тайцы"),
    ("Узбеки в Корее", "🇺🇿", "Узбеки"),
    ("Казахи в Корее", "🇰🇿", "Казахи"),
    ("Непальцы в Корее", "🇳🇵", "Непальцы"),
    ("Другие национальности", "🌏", "Общая группа"),
]

# Guide sections. Articles stay unpublished until content and a source are added.
ARTICLE_SECTIONS = [
    ("visas", "Визы и документы"),
    ("registration", "Регистрация и адрес"),
    ("medicine", "Медицина"),
    ("banks", "Банки и мобильная связь"),
    ("housing", "Жильё"),
    ("work", "Работа и трудовые права"),
    ("emergency", "Экстренные ситуации"),
]


def seed_reference(db: Session) -> None:
    ensure_pricing(db)
    ensure_geo(db)
    if db.scalar(select(func.count()).select_from(Group)) == 0:
        for name, flag, description in GROUPS:
            db.add(Group(name=name, flag=flag, description=description, telegram_link=""))
    if db.scalar(select(func.count()).select_from(Article)) == 0:
        for section, title in ARTICLE_SECTIONS:
            db.add(Article(section=section, title=title, body="", source_url="", published=False))
    db.commit()
