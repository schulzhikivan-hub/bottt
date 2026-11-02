#!/usr/bin/env bash
# Quick check of a running KOREA HUB after an update:  bash tools/smoke.sh https://your-domain
# Opens the main public pages and API answers and says which one is wrong. It changes nothing.
set -u
base="${1:-${WEBAPP_URL:-}}"
if [ -z "$base" ]; then
  echo "Укажите адрес сайта: bash tools/smoke.sh https://your-domain" >&2
  exit 2
fi
base="${base%/}"
bad=0
check() {  # name path expected-text-or-empty
  local name="$1" path="$2" want="${3:-}" code body
  body="$(curl -sS --max-time 15 -o /tmp/smoke.$$ -w '%{http_code}' "$base$path" 2>/dev/null)" || body="000"
  code="$body"
  if [ "$code" != "200" ]; then
    echo "✗ $name ($path): ответ $code"
    bad=$((bad + 1))
    return
  fi
  if [ -n "$want" ] && ! grep -q "$want" /tmp/smoke.$$ 2>/dev/null; then
    echo "✗ $name ($path): в ответе нет «$want»"
    bad=$((bad + 1))
    return
  fi
  echo "✓ $name"
}
check "Сервер жив" "/health" "ok"
check "Главная страница" "/" "KOREA HUB"
check "Каталог объявлений" "/api/catalog?limit=1" "items"
check "Услуги и цены" "/api/services" "code"
check "Обменник KH" "/api/exchange" "kh_per_star"
check "Почта (включена или нет)" "/api/mail-state" "enabled"
check "Карта сайта" "/sitemap.xml" "urlset"
check "robots.txt" "/robots.txt" "Sitemap"
check "Админ-панель" "/admin" "html"
rm -f "/tmp/smoke.$$"
if command -v docker >/dev/null 2>&1 && [ -f docker-compose.yml ]; then
  errors="$(docker compose logs --since 10m web 2>/dev/null | grep -c '"level": "ERROR"' || true)"
  if [ "${errors:-0}" -gt 0 ]; then
    echo "✗ В журнале сервера за 10 минут ошибок: $errors. Посмотрите: docker compose logs --since 10m web | grep ERROR"
    bad=$((bad + 1))
  else
    echo "✓ В журнале сервера за 10 минут ошибок нет"
  fi
fi
if [ "$bad" -eq 0 ]; then
  echo "Всё в порядке."
else
  echo "Проблем: $bad"
  exit 1
fi
