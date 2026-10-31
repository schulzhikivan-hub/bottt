#!/usr/bin/env bash
# KOREA HUB installer and updater for a VPS (Ubuntu 22.04+ or Debian 12).
#
#   sudo bash install.sh install   первая установка
#   sudo bash install.sh update    обновление до последнего коммита ветки
#   sudo bash install.sh backup    резервная копия базы сейчас
#   sudo bash install.sh status    состояние сервисов
#
# Неинтерактивный режим: задайте DOMAIN, BOT_TOKEN, ADMIN_IDS в окружении.
# Полная инструкция: docs/INSTALL.md
set -Eeuo pipefail

REPO_URL="${REPO_URL:-https://github.com/schulzhikivan-hub/bottt.git}"
BRANCH="${BRANCH:-korea-hub}"
APP_DIR="${APP_DIR:-/opt/korea-hub}"
BACKUP_DIR="$APP_DIR/backups"
HEALTH_TIMEOUT=120

trap 'printf "\n\033[1;31mОстановлено из-за ошибки (строка %s). Изменения, сделанные до этого, сохранены.\033[0m\n" "$LINENO" >&2' ERR

log()  { printf '\n\033[1;34m==>\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33mВНИМАНИЕ:\033[0m %s\n' "$*" >&2; }
die()  { printf '\033[1;31mОШИБКА:\033[0m %s\n' "$*" >&2; exit 1; }

require_root() {
  [ "$(id -u)" -eq 0 ] || die "Запустите от root: sudo bash install.sh $1"
}

require_supported_os() {
  # shellcheck disable=SC1091
  . /etc/os-release
  case "${ID:-}" in
    ubuntu|debian) ;;
    *) die "Поддерживаются Ubuntu и Debian. Сейчас: ${ID:-неизвестно}." ;;
  esac
}

# ask VAR "вопрос" "значение по умолчанию"
# Если переменная уже задана в окружении, вопрос не задаётся.
ask() {
  local var="$1" prompt="$2" default="${3:-}" value=""
  if [ -n "${!var:-}" ]; then
    return 0
  fi
  if [ -r /dev/tty ]; then
    read -r -p "$prompt${default:+ [$default]}: " value </dev/tty || value=""
  fi
  printf -v "$var" '%s' "${value:-$default}"
}

install_packages() {
  log "Устанавливаю git, curl, openssl и Docker"
  export DEBIAN_FRONTEND=noninteractive
  apt-get update -y
  apt-get install -y git curl ca-certificates openssl
  if ! command -v docker >/dev/null 2>&1; then
    curl -fsSL https://get.docker.com | sh
  fi
  systemctl enable --now docker
  docker compose version >/dev/null 2>&1 || die "Не найден плагин docker compose."
}

sync_repo() {
  if [ ! -d "$APP_DIR/.git" ]; then
    log "Клонирую $REPO_URL (ветка $BRANCH) в $APP_DIR"
    git clone --branch "$BRANCH" "$REPO_URL" "$APP_DIR"
  else
    log "Обновляю код до origin/$BRANCH"
    git -C "$APP_DIR" fetch --quiet origin "$BRANCH"
    # Файлы на сервере не редактируем. Локальные изменения кода будут сброшены.
    git -C "$APP_DIR" reset --hard "origin/$BRANCH"
  fi
}

write_env() {
  local env_file="$APP_DIR/.env"
  if [ -f "$env_file" ]; then
    log "Найден $env_file, оставляю без изменений"
    return 0
  fi

  log "Настройка. Ответьте на вопросы (значения по умолчанию в скобках)"
  ask DOMAIN "Домен для Mini App, A-запись должна указывать на этот сервер (например app.example.com)" ""
  [ -n "${DOMAIN:-}" ] || die "Нужен домен. Mini App работает только по HTTPS."
  ask BOT_TOKEN "Токен бота от @BotFather (можно пусто, тогда бот не запустится)" ""
  ask ADMIN_IDS "Telegram ID администраторов через запятую (можно пусто)" ""
  [ -n "${ADMIN_IDS:-}" ] || warn "ADMIN_IDS пуст. Модерация через бота будет недоступна, добавьте ID позже в $env_file."

  local pg_pass admin_key
  pg_pass="$(openssl rand -hex 24)"
  admin_key="$(openssl rand -hex 32)"

  umask 077
  cat >"$env_file" <<EOF
DOMAIN=$DOMAIN
WEBAPP_URL=https://$DOMAIN
BOT_TOKEN=$BOT_TOKEN
ADMIN_IDS=$ADMIN_IDS
ADMIN_API_KEY=$admin_key
POSTGRES_PASSWORD=$pg_pass
EOF
  chmod 600 "$env_file"
  log "Файл $env_file создан. Пароли сгенерированы автоматически и показаны не будут."
}

db_is_running() {
  (cd "$APP_DIR" && docker compose exec -T db pg_isready -U koreahub -d koreahub >/dev/null 2>&1)
}

backup_db() {
  if ! db_is_running; then
    warn "База не запущена, резервная копия пропущена (первая установка или сбой)."
    return 0
  fi
  mkdir -p "$BACKUP_DIR"
  local stamp file
  stamp="$(date +%Y%m%d-%H%M%S)"
  file="$BACKUP_DIR/koreahub-$stamp.sql.gz"
  log "Резервная копия базы: $file"
  (cd "$APP_DIR" && docker compose exec -T db pg_dump -U koreahub koreahub) | gzip >"$file"
  [ -s "$file" ] || die "Резервная копия пустая. Обновление остановлено."
  # Храним 14 дней
  find "$BACKUP_DIR" -name 'koreahub-*.sql.gz' -mtime +14 -delete
}

deploy() {
  cd "$APP_DIR"
  log "Собираю и запускаю контейнеры"
  docker compose up -d --build --remove-orphans

  log "Жду готовности API (до ${HEALTH_TIMEOUT} с)"
  local i
  for ((i = 0; i < HEALTH_TIMEOUT / 3; i++)); do
    if docker compose exec -T api python -c \
      "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=3).status==200 else 1)" \
      >/dev/null 2>&1; then
      echo "API отвечает."
      break
    fi
    if [ "$i" -eq $((HEALTH_TIMEOUT / 3 - 1)) ]; then
      docker compose logs --tail=80 api
      die "API не поднялся за ${HEALTH_TIMEOUT} с. Логи выше."
    fi
    sleep 3
  done

  local domain
  domain="$(grep -E '^DOMAIN=' .env | cut -d= -f2-)"
  log "Проверяю HTTPS: https://$domain/health"
  if curl -fsS --max-time 15 "https://$domain/health" >/dev/null 2>&1; then
    echo "HTTPS работает."
  else
    warn "HTTPS пока не отвечает. Сертификат выдаётся не сразу. Проверьте DNS и открыты ли порты 80 и 443. Повторите: sudo bash $APP_DIR/install.sh status"
  fi
}

print_summary() {
  cat <<EOF

Готово.

Mini App:  https://$(grep -E '^DOMAIN=' "$APP_DIR/.env" | cut -d= -f2-)
Каталог:   $APP_DIR
Логи:      cd $APP_DIR && docker compose logs -f
Обновить:  sudo bash $APP_DIR/install.sh update
Бэкап:     sudo bash $APP_DIR/install.sh backup

Дальше в Telegram: в @BotFather укажите Mini App URL (см. docs/INSTALL.md, раздел 4).
EOF
}

cmd_install() {
  require_root install
  require_supported_os
  install_packages
  sync_repo
  write_env
  deploy
  print_summary
}

cmd_update() {
  require_root update
  require_supported_os
  [ -d "$APP_DIR/.git" ] || die "$APP_DIR не найден. Сначала выполните: sudo bash install.sh install"
  backup_db
  sync_repo
  deploy
  print_summary
}

cmd_status() {
  [ -d "$APP_DIR" ] || die "$APP_DIR не найден."
  cd "$APP_DIR"
  docker compose ps
  git -C "$APP_DIR" log -1 --oneline
}

usage() {
  cat <<EOF
Использование: sudo bash install.sh {install|update|backup|status}
  install  первая установка
  update   обновить код и перезапустить сервисы (с резервной копией базы)
  backup   резервная копия базы
  status   состояние сервисов
EOF
}

main() {
  case "${1:-}" in
    install) cmd_install ;;
    update)  cmd_update ;;
    backup)  require_root backup; cmd_backup_only ;;
    status)  cmd_status ;;
    *)       usage; exit 1 ;;
  esac
}

cmd_backup_only() {
  [ -d "$APP_DIR/.git" ] || die "$APP_DIR не найден."
  backup_db
}

main "$@"
