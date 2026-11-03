#!/usr/bin/env bash
# KOREA HUB installer and updater for a VPS (Ubuntu 22.04+ or Debian 12).
#
#   sudo bash install.sh install   первая установка
#   sudo bash install.sh update    обновление до последнего коммита ветки
#   sudo bash install.sh backup    полная копия сейчас: база и фото в одном файле
#   sudo bash install.sh restore ФАЙЛ   восстановить базу и фото из копии
#   sudo bash install.sh set-token замена токена бота (ввод скрыт)
#   sudo bash install.sh monitor   проверка здоровья; о сбое пишет администраторам (запускается по расписанию сама)
#   sudo bash install.sh cron      включить ежедневную копию в 04:00 и проверку каждые 5 минут
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

TOKEN_FILE="${TOKEN_FILE:-/root/.config/korea-hub/github_token}"
GIT_CFG=()

# Токен нужен только для приватного репозитория (права Contents: Read-only).
# Токен передаётся git через заголовок, поэтому не попадает в URL и .git/config.
ask_github_token() {
  if [ -z "${GITHUB_TOKEN:-}" ] && [ ! -f "$TOKEN_FILE" ] && [ -r /dev/tty ]; then
    # Скрытый ввод: токен не отображается на экране
    read -r -s -p "Токен GitHub для приватного репозитория (ввод скрыт, Enter, если репозиторий публичный): " GITHUB_TOKEN </dev/tty || GITHUB_TOKEN=""
    echo
  fi
  if [ -n "${GITHUB_TOKEN:-}" ]; then
    mkdir -p "$(dirname "$TOKEN_FILE")"
    umask 077
    printf '%s' "$GITHUB_TOKEN" >"$TOKEN_FILE"
    chmod 600 "$TOKEN_FILE"
    log "Токен сохранён в $TOKEN_FILE (доступ только root)"
  fi
}

load_github_token() {
  local token="${GITHUB_TOKEN:-}"
  if [ -z "$token" ] && [ -f "$TOKEN_FILE" ]; then
    token="$(cat "$TOKEN_FILE")"
  fi
  if [ -n "$token" ]; then
    local basic
    basic="$(printf 'x-access-token:%s' "$token" | base64 | tr -d '\n')"
    GIT_CFG=(-c "http.extraheader=Authorization: Basic $basic")
  fi
}

sync_repo() {
  if [ ! -d "$APP_DIR/.git" ]; then
    log "Клонирую $REPO_URL (ветка $BRANCH) в $APP_DIR"
    if ! git "${GIT_CFG[@]}" clone --branch "$BRANCH" "$REPO_URL" "$APP_DIR"; then
      die "Не удалось клонировать репозиторий. Если он приватный, укажите токен GitHub (см. docs/INSTALL.md, раздел 3)."
    fi
  else
    log "Обновляю код до origin/$BRANCH"
    git "${GIT_CFG[@]}" -C "$APP_DIR" fetch --quiet origin "$BRANCH"
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
  if [ -z "${DOMAIN:-}" ] && [ ! -r /dev/tty ]; then
    die "Нет терминала для ввода. Укажите домен: DOMAIN=app.example.com sudo bash install.sh install"
  fi
  # Домен обязателен: переспрашиваем, пока не введут
  while [ -z "${DOMAIN:-}" ]; do
    ask DOMAIN "Домен для Mini App, A-запись должна указывать на этот сервер (например app.example.com)" ""
    [ -n "${DOMAIN:-}" ] || warn "Домен обязателен. Введите его и нажмите Enter."
  done
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

env_value() {
  grep -E "^$1=" "$APP_DIR/.env" | head -n1 | cut -d= -f2-
}

# Сообщение всем администраторам через Bot API. Токен передаётся через stdin, а не в аргументах: его не видно в списке процессов.
tg_send() {
  local token ids id
  local -a list
  token="$(env_value BOT_TOKEN)"
  ids="$(env_value ADMIN_IDS)"
  { [ -n "$token" ] && [ -n "$ids" ]; } || return 0
  IFS=',' read -ra list <<<"$ids"
  for id in "${list[@]}"; do
    id="${id// /}"
    [[ "$id" =~ ^[0-9]+$ ]] || continue
    printf 'url = "https://api.telegram.org/bot%s/sendMessage"\n' "$token" |
      curl -sS --max-time 10 -K - --data-urlencode "chat_id=$id" --data-urlencode "text=$1" >/dev/null 2>&1 || true
  done
}

# Полная копия: база и фото в одном файле. Хранится 14 дней. Печатает путь к файлу.
backup_full() {
  if ! db_is_running; then
    warn "База не запущена, резервная копия пропущена (первая установка или сбой)."
    return 0
  fi
  mkdir -p "$BACKUP_DIR"
  chmod 700 "$BACKUP_DIR"
  local stamp out tmp
  stamp="$(date +%Y%m%d-%H%M%S)"
  out="$BACKUP_DIR/koreahub-full-$stamp.tar.gz"
  tmp="$(mktemp -d)"
  log "Полная копия (база и фото): $out"
  (cd "$APP_DIR" && docker compose exec -T db pg_dump -U koreahub koreahub) >"$tmp/db.sql"
  if [ ! -s "$tmp/db.sql" ]; then
    rm -rf "$tmp"
    die "Копия базы пустая. Действие остановлено."
  fi
  (cd "$APP_DIR" && docker compose exec -T api tar -C /srv -cf - uploads) >"$tmp/uploads.tar" 2>/dev/null ||
    warn "Фото не удалось скопировать (сервис api не запущен?). В копии будет только база."
  (umask 077 && tar -czf "$out" -C "$tmp" db.sql uploads.tar)
  rm -rf "$tmp"
  find "$BACKUP_DIR" -name 'koreahub-*.tar.gz' -mtime +14 -delete
  find "$BACKUP_DIR" -name 'koreahub-*.sql.gz' -mtime +14 -delete
  echo "$out"
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
  ask_github_token
  load_github_token
  sync_repo
  write_env
  deploy
  setup_cron
  print_summary
}

cmd_update() {
  require_root update
  require_supported_os
  [ -d "$APP_DIR/.git" ] || die "$APP_DIR не найден. Сначала выполните: sudo bash install.sh install"
  [ -f "$APP_DIR/.env" ] || die "Нет файла $APP_DIR/.env: установка не была завершена. Выполните: sudo bash install.sh install"
  load_github_token
  backup_full >/dev/null
  sync_repo
  deploy
  setup_cron
  print_summary
}

cmd_status() {
  [ -d "$APP_DIR" ] || die "$APP_DIR не найден."
  cd "$APP_DIR"
  docker compose ps
  git -C "$APP_DIR" log -1 --oneline
}

# Расписание: копия каждую ночь в 04:00 и проверка здоровья каждые 5 минут
setup_cron() {
  if [ ! -d /etc/cron.d ]; then
    warn "Нет /etc/cron.d: расписание не настроено. Копию можно делать вручную: sudo bash $APP_DIR/install.sh backup"
    return 0
  fi
  cat >/etc/cron.d/korea-hub <<EOF
SHELL=/bin/bash
PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin
0 4 * * * root bash $APP_DIR/install.sh backup >>/var/log/korea-hub-backup.log 2>&1
*/5 * * * * root bash $APP_DIR/install.sh monitor >>/var/log/korea-hub-monitor.log 2>&1
EOF
  chmod 644 /etc/cron.d/korea-hub
  log "Расписание включено: копия в 04:00 и проверка каждые 5 минут (/etc/cron.d/korea-hub)"
}

cmd_cron() {
  require_root cron
  [ -f "$APP_DIR/.env" ] || die "Нет $APP_DIR/.env: установка не была завершена."
  setup_cron
}

# Копия вне сервера. Включается файлом /etc/korea-hub/offsite с одной строкой user@host:/папка
# и ключом /etc/korea-hub/offsite_key (без пароля, только на запись в эту папку). Нет файла: ничего не делаем.
OFFSITE_CONF="/etc/korea-hub/offsite"
OFFSITE_KEY="/etc/korea-hub/offsite_key"
offsite_push() {
  local file="$1" target
  [ -f "$OFFSITE_CONF" ] || return 0
  [ -f "$file" ] || return 0
  [ -f "$OFFSITE_KEY" ] || { warn "Копия вне сервера включена, но нет ключа $OFFSITE_KEY."; return 0; }
  target="$(head -n1 "$OFFSITE_CONF" | tr -d '[:space:]')"
  case "$target" in
    *@*:*) ;;
    *) warn "В $OFFSITE_CONF должна быть строка вида user@host:/папка"; return 0 ;;
  esac
  if scp -q -i "$OFFSITE_KEY" -o BatchMode=yes -o ConnectTimeout=15 -o StrictHostKeyChecking=accept-new "$file" "$target/"; then
    log "Копия отправлена вне сервера: $target"
  else
    warn "Не удалось отправить копию вне сервера ($target). Проверьте ключ и доступ."
    return 1
  fi
}

cmd_offsite() {
  require_root offsite
  [ -f "$OFFSITE_CONF" ] || die "Не настроено. Создайте $OFFSITE_CONF (одна строка user@host:/папка) и ключ $OFFSITE_KEY. См. docs/RESTORE.md."
  local latest="" f
  for f in "$BACKUP_DIR"/koreahub-full-*.tar.gz; do   # имена с датой: последний в алфавитном порядке самый свежий
    [ -f "$f" ] && latest="$f"
  done
  [ -n "$latest" ] || die "Копий пока нет. Сначала: sudo bash $APP_DIR/install.sh backup"
  offsite_push "$latest" || die "Отправка не удалась."
}

cmd_backup_only() {
  [ -d "$APP_DIR/.git" ] || die "$APP_DIR не найден."
  local out
  out="$(backup_full)"
  echo "$out"
  offsite_push "$out" || true
}

# Восстановление из копии. Перед заменой текущее состояние сохраняется отдельной копией.
cmd_restore() {
  require_root restore
  local file="${1:-}" tmp answer
  { [ -n "$file" ] && [ -f "$file" ]; } || die "Укажите файл копии, например: sudo bash install.sh restore $BACKUP_DIR/koreahub-full-XXXX.tar.gz"
  [ -d "$APP_DIR/.git" ] || die "$APP_DIR не найден."
  warn "Восстановление ЗАМЕНИТ текущую базу и фото данными из файла: $file"
  if [ "${FORCE:-}" != "1" ]; then
    read -r -p "Чтобы продолжить, напишите ДА: " answer </dev/tty || die "Нет терминала. Для запуска без вопросов: FORCE=1 sudo bash install.sh restore ФАЙЛ"
    [ "$answer" = "ДА" ] || die "Отменено. Ничего не изменено."
  fi
  log "Сначала сохраняю текущее состояние отдельной копией"
  backup_full >/dev/null || warn "Не удалось сохранить текущее состояние."
  tmp="$(mktemp -d)"
  case "$file" in
    *.tar.gz)
      tar -xzf "$file" -C "$tmp"
      [ -s "$tmp/db.sql" ] || { rm -rf "$tmp"; die "В архиве нет db.sql."; }
      ;;
    *.sql.gz) gzip -dc "$file" >"$tmp/db.sql" ;;
    *) rm -rf "$tmp"; die "Неизвестный формат: нужен файл .tar.gz или .sql.gz" ;;
  esac
  cd "$APP_DIR"
  docker compose stop api bot
  docker compose exec -T db psql -U koreahub -d postgres -v ON_ERROR_STOP=1 -c "DROP DATABASE IF EXISTS koreahub WITH (FORCE)"
  docker compose exec -T db psql -U koreahub -d postgres -v ON_ERROR_STOP=1 -c "CREATE DATABASE koreahub OWNER koreahub"
  docker compose exec -T db psql -U koreahub -d koreahub -v ON_ERROR_STOP=1 -q <"$tmp/db.sql" >/dev/null
  if [ -s "$tmp/uploads.tar" ]; then
    docker compose run --rm -T --no-deps --entrypoint sh api -c 'rm -rf /srv/uploads/* /srv/uploads/.[!.]* 2>/dev/null; tar -C /srv -xf -' <"$tmp/uploads.tar"
  fi
  rm -rf "$tmp"
  docker compose up -d
  log "Готово: база и фото восстановлены из $file"
}

# Проверка здоровья по расписанию. Пишет администраторам только при изменении: сбой (после двух проверок подряд),
# восстановление, остановка бота, скорое окончание сертификата.
cmd_monitor() {
  [ -f "$APP_DIR/.env" ] || exit 0
  local domain state body fails=0 down=0 warned="" msg="" days
  domain="$(env_value DOMAIN)"
  state="$APP_DIR/.monitor_state"
  if [ -f "$state" ]; then
    fails="$(grep -E '^fails=' "$state" | cut -d= -f2-)"
    down="$(grep -E '^down=' "$state" | cut -d= -f2-)"
    warned="$(grep -E '^warned=' "$state" | cut -d= -f2-)"
  fi
  fails="${fails:-0}"
  down="${down:-0}"
  if body="$(curl -fsS --max-time 10 "https://$domain/health/extended" 2>/dev/null)"; then
    if [ "$down" = "1" ]; then
      tg_send "🟢 KOREA HUB снова работает."
    fi
    fails=0
    down=0
    case "$body" in
      *'"bot":"stale"'*) msg="бот не подаёт признаков жизни больше 5 минут. Проверьте: docker compose logs bot" ;;
    esac
    days="$(printf '%s' "$body" | sed -n 's/.*"cert_days":\(-\{0,1\}[0-9]\{1,\}\).*/\1/p')"
    if [ -n "$days" ] && [ "$days" -lt 10 ]; then
      msg="${msg:+$msg. }сертификат HTTPS истекает через $days дн."
    fi
    if [ -n "$msg" ] && [ "$msg" != "$warned" ]; then
      tg_send "⚠️ KOREA HUB: $msg"
    fi
    warned="$msg"
  else
    fails=$((fails + 1))
    if [ "$fails" -ge 2 ] && [ "$down" != "1" ]; then
      tg_send "🔴 KOREA HUB не отвечает ($domain). Две проверки подряд неудачны. На сервере: cd $APP_DIR && docker compose ps && docker compose logs --tail=50 api"
      down=1
    fi
  fi
  printf 'fails=%s\ndown=%s\nwarned=%s\n' "$fails" "$down" "$warned" >"$state"
}

# Замена токена бота. Токен вводится скрыто (или NEW_BOT_TOKEN в окружении), проверяется у Telegram и нигде не печатается.
cmd_set_token() {
  require_root set-token
  [ -f "$APP_DIR/.env" ] || die "Нет $APP_DIR/.env: установка не была завершена."
  local token reply tmp name
  if [ -n "${NEW_BOT_TOKEN:-}" ]; then
    token="$NEW_BOT_TOKEN"
  else
    read -rsp "Новый токен от @BotFather (ввод не виден): " token </dev/tty || die "Нет терминала. Укажите: NEW_BOT_TOKEN=... sudo -E bash install.sh set-token"
    echo
  fi
  [[ "$token" =~ ^[0-9]{6,12}:[A-Za-z0-9_-]{30,}$ ]] || die "Это не похоже на токен бота. Он выглядит как 123456789:AAH... (его выдаёт @BotFather)."
  reply="$(printf 'url = "https://api.telegram.org/bot%s/getMe"\n' "$token" | curl -sS --max-time 15 -K - 2>/dev/null || true)"
  [[ "$reply" == *'"ok":true'* ]] || die "Telegram не принял этот токен. Проверьте, что скопировали его целиком и что он не отозван."
  tmp="$(mktemp)"
  TOKEN="$token" awk 'BEGIN { t = ENVIRON["TOKEN"]; done = 0 }
    /^BOT_TOKEN=/ { print "BOT_TOKEN=" t; done = 1; next }
    { print }
    END { if (!done) print "BOT_TOKEN=" t }' "$APP_DIR/.env" >"$tmp"
  cat "$tmp" >"$APP_DIR/.env"
  chmod 600 "$APP_DIR/.env"
  rm -f "$tmp"
  (cd "$APP_DIR" && docker compose up -d --force-recreate api bot)
  name="$(printf '%s' "$reply" | sed -n 's/.*"username":"\([^"]*\)".*/\1/p')"
  log "Токен заменён, сервисы перезапущены. Бот: @${name:-?}. Старый токен перестанет работать после /revoke в @BotFather."
}

usage() {
  cat <<EOF
Использование: sudo bash install.sh {install|update|backup|restore|set-token|monitor|cron|offsite|status}
  install     первая установка
  update      обновить код и перезапустить сервисы (с полной копией перед этим)
  backup      полная копия: база и фото в одном файле (хранится 14 дней)
  restore     восстановить из копии: sudo bash install.sh restore ФАЙЛ
  set-token   заменить токен бота (ввод скрыт)
  monitor     проверить здоровье, при сбое написать администраторам
  cron        включить ежедневную копию (04:00) и проверку каждые 5 минут
  offsite     отправить последнюю копию вне сервера (настройка в docs/RESTORE.md)
  status      состояние сервисов
EOF
}

main() {
  case "${1:-}" in
    install)   cmd_install ;;
    update)    cmd_update ;;
    backup)    require_root backup; cmd_backup_only ;;
    restore)   cmd_restore "${2:-}" ;;
    set-token) cmd_set_token ;;
    monitor)   cmd_monitor ;;
    cron)      cmd_cron ;;
    offsite)   cmd_offsite ;;
    status)    cmd_status ;;
    *)         usage; exit 1 ;;
  esac
}

main "$@"
