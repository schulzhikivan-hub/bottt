#!/usr/bin/env bash
# Запускать от владельца, не от root.
# Записывает IP самого VPS и IP через прокси Chrome (если задан) в ~/ip-log.txt,
# чтобы видеть, меняется ли IP.
set -euo pipefail

CHROME_PROXY=""
conf="$HOME/.config/tiktok-chrome.env"
# shellcheck source=/dev/null
[ -f "$conf" ] && source "$conf"

now="$(date -Is)"
vps="$(curl -fsS https://ipinfo.io/json | tr -d '\n ')"
printf '%s vps %s\n' "$now" "$vps" | tee -a "$HOME/ip-log.txt"

if [ -n "$CHROME_PROXY" ]; then
  if via="$(curl -fsS -x "$CHROME_PROXY" https://ipinfo.io/json | tr -d '\n ')"; then
    printf '%s proxy %s\n' "$now" "$via" | tee -a "$HOME/ip-log.txt"
  else
    echo "Прокси $CHROME_PROXY не ответил. Если он требует логин, проверьте так:" >&2
    echo "  curl -x $CHROME_PROXY -U ЛОГИН https://ipinfo.io/json" >&2
    exit 1
  fi
fi
