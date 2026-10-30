#!/usr/bin/env bash
# Одноразовая установка с нуля. Запускается из workflow bootstrap.yml через ssh.
# Аргумент: путь к файлу с переменными (его загружает workflow и удаляет после чтения).
# Делает: пользователя owner с ключом и паролем, Tailscale, self-hosted runner,
# sudoers для runner, затем полную установку setup/install.sh.
set -euo pipefail

ENV_FILE="${1:?укажите файл с переменными}"
# shellcheck source=/dev/null
source "$ENV_FILE"
shred -u "$ENV_FILE" 2>/dev/null || rm -f "$ENV_FILE"

REPO_DIR=/opt/bottt
# shellcheck source=lib.sh
source "$REPO_DIR/setup/lib.sh"
require_root

for var in OWNER_PUBKEY OWNER_PASSWORD TS_AUTHKEY RUNNER_TOKEN REPO_URL REPO_NAME; do
  [ -n "${!var:-}" ] || die "не задана переменная $var"
done

log "Пользователь $OWNER_USER с ключом и паролем"
ensure_owner_user
owner_home="$(getent passwd "$OWNER_USER" | cut -d: -f6)"
install -d -m 700 -o "$OWNER_USER" -g "$OWNER_USER" "$owner_home/.ssh"
printf '%s\n' "$OWNER_PUBKEY" > "$owner_home/.ssh/authorized_keys"
chown "$OWNER_USER:$OWNER_USER" "$owner_home/.ssh/authorized_keys"
chmod 600 "$owner_home/.ssh/authorized_keys"
printf '%s:%s\n' "$OWNER_USER" "$OWNER_PASSWORD" | chpasswd

log "Tailscale"
if ! command -v tailscale >/dev/null 2>&1; then
  curl -fsSL https://tailscale.com/install.sh | sh
fi
tailscale up --authkey="$TS_AUTHKEY" --hostname=vps-tiktok

log "GitHub Actions runner"
runner_home=/home/gh-runner/actions-runner
if ! id gh-runner >/dev/null 2>&1; then
  adduser --disabled-password --gecos "" gh-runner
fi
apt_install curl tar
version="$(curl -fsSL https://api.github.com/repos/actions/runner/releases/latest \
  | grep -o '"tag_name": *"v[^"]*"' | head -n 1 | sed 's/.*"v\([^"]*\)"/\1/')"
[ -n "$version" ] || die "не удалось узнать версию runner"
install -d -o gh-runner -g gh-runner "$runner_home"
runuser -u gh-runner -- bash -c "
  cd '$runner_home' &&
  curl -fsSL -o runner.tgz 'https://github.com/actions/runner/releases/download/v$version/actions-runner-linux-x64-$version.tar.gz' &&
  tar xzf runner.tgz && rm -f runner.tgz &&
  ./config.sh --unattended --url '$REPO_URL' --token '$RUNNER_TOKEN' \
    --labels vps --name vps-tiktok --replace
"
cd "$runner_home"
./svc.sh install gh-runner
./svc.sh start

log "Права runner на установку"
sudoers_file=/etc/sudoers.d/gh-runner
printf 'gh-runner ALL=(root) NOPASSWD: %s/_work/%s/%s/setup/install.sh\n' \
  "$runner_home" "$REPO_NAME" "$REPO_NAME" > "$sudoers_file"
chmod 440 "$sudoers_file"
visudo -cf "$sudoers_file" >/dev/null || { rm -f "$sudoers_file"; die "sudoers не прошёл проверку"; }

log "Полная установка"
bash "$REPO_DIR/setup/install.sh"

log "Готово. Runner, Tailscale и установка выполнены."
