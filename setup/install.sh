#!/usr/bin/env bash
# Точка входа: применяет все шаги установки по порядку.
# Идемпотентен: повторный запуск безопасен и ничего не ломает.
set -euo pipefail

SETUP_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SETUP_DIR/lib.sh"
require_root

for step in 01-base 02-desktop 03-chrome 04-security; do
  bash "$SETUP_DIR/$step.sh"
done

log "Проверка сервисов"
systemctl is-active --quiet xrdp && echo "xrdp: active"
systemctl is-active --quiet fail2ban && echo "fail2ban: active"
ufw status | head -n 1
echo "Готово."
