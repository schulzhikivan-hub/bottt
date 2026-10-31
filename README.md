# KOREA HUB

Telegram-бот и Mini App для иностранцев в Южной Корее: работа, жильё, барахолка, знакомства, сообщество.

Статус: MVP. Полная спецификация — в `docs/`.

## Документы
- `docs/KOREA-HUB-PTZ.md` — полное техническое задание (сводное).
- `docs/korea-hub-mini-app-spec.md` — спецификация Mini App.
- `docs/tz-2-monetization.md` — монетизация через Telegram Stars.
- `docs/INSTALL.md` — установка и обновление на VPS.

## Установка на VPS
```bash
sudo bash install.sh install
```
Подробно: `docs/INSTALL.md`.

## Обновление
```bash
sudo bash /opt/korea-hub/install.sh update
```

## Разработка
```bash
pip install -r requirements.txt
pytest -q          # тесты работают только на временной SQLite-базе
```

Локальный запуск через Docker: скопируйте `.env.example` в `.env`, заполните `POSTGRES_PASSWORD` и `DOMAIN`, затем `docker compose up --build`.

## Важно
- Не коммитьте `.env`, токены ботов, пароли и ключи SSH.
- Перед публичным запуском: ограничение частоты запросов, хранение загружаемых фото, журнал аудита, страницы правил и конфиденциальности, форма жалоб, резервные копии, юридическая проверка (§21 ПТЗ).
- Миграции Alembic пока не подключены: таблицы создаются при запуске API. Это нужно закрыть до первого обновления с изменением схемы.
