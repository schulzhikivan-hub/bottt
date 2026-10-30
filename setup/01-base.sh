#!/usr/bin/env bash
# Базовая подготовка: индексы пакетов, часовой пояс, пользователь владельца.
set -euo pipefail

source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"
require_root

log "Индексы пакетов"
DEBIAN_FRONTEND=noninteractive apt-get update

log "Часовой пояс: $TIMEZONE"
timedatectl set-timezone "$TIMEZONE"

log "Пользователь $OWNER_USER"
ensure_owner_user
if passwd -S "$OWNER_USER" | grep -q ' NP '; then
  echo "ВНИМАНИЕ: у $OWNER_USER нет пароля. Он нужен для входа по RDP: sudo passwd $OWNER_USER"
fi

apt_install curl wget ca-certificates gnupg
