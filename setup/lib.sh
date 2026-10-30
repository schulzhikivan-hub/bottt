#!/usr/bin/env bash
# Общие функции и настройки для всех скриптов установки. Подключается через source.

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
# shellcheck source=settings.env
source "$REPO_DIR/setup/settings.env"

log() {
  printf '\n==> %s\n' "$*"
}

die() {
  printf 'ОШИБКА: %s\n' "$*" >&2
  exit 1
}

require_root() {
  [ "$(id -u)" -eq 0 ] || die "запускайте через sudo или от root"
}

# Ставит только то, чего нет. Повторный запуск ничего лишнего не делает.
apt_install() {
  local missing=() pkg
  for pkg in "$@"; do
    dpkg -s "$pkg" >/dev/null 2>&1 || missing+=("$pkg")
  done
  if [ "${#missing[@]}" -gt 0 ]; then
    DEBIAN_FRONTEND=noninteractive apt-get install -y "${missing[@]}"
  fi
}

# Команда от имени владельца (для файлов в его домашней папке).
as_owner() {
  runuser -u "$OWNER_USER" -- "$@"
}

ensure_owner_user() {
  if ! id "$OWNER_USER" >/dev/null 2>&1; then
    adduser --disabled-password --gecos "" "$OWNER_USER"
  fi
  usermod -aG sudo "$OWNER_USER"
}
