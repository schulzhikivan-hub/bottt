#!/usr/bin/env bash
# Безопасность: SSH только по ключам, ufw, fail2ban.
set -euo pipefail

source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"
require_root

log "SSH: вход только по ключу"
keys="/home/$OWNER_USER/.ssh/authorized_keys"
[ -s "$keys" ] || die "нет ключей в $keys. Сначала добавьте свой публичный ключ, иначе отключение паролей закроет доступ"

sshd_conf=/etc/ssh/sshd_config.d/01-owner-hardening.conf
install -m 0644 "$REPO_DIR/configs/ssh/01-owner-hardening.conf" "$sshd_conf"
if ! sshd -t; then
  rm -f "$sshd_conf"
  die "sshd -t не прошёл, конфиг откачен"
fi
systemctl reload ssh

log "Фаервол ufw"
apt_install ufw
ufw default deny incoming
ufw default allow outgoing
while read -r rule; do
  case "$rule" in
    ''|\#*) continue ;;
  esac
  read -r -a args <<< "$rule"
  ufw "${args[@]}" < /dev/null
done < "$REPO_DIR/configs/ufw/rules.txt"
ufw --force enable

log "fail2ban"
apt_install fail2ban
install -m 0644 "$REPO_DIR/configs/fail2ban/jail.local" /etc/fail2ban/jail.local
systemctl enable fail2ban
systemctl restart fail2ban
