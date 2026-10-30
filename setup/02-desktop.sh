#!/usr/bin/env bash
# Рабочий стол XFCE и RDP-сервер xrdp.
# Внимание: перезапуск xrdp обрывает текущую RDP-сессию.
set -euo pipefail

source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"
require_root

log "XFCE и xrdp"
apt_install xfce4 xfce4-terminal xrdp
usermod -aG ssl-cert xrdp

log "Сессия XFCE для $OWNER_USER"
home="$(getent passwd "$OWNER_USER" | cut -d: -f6)"
install -o "$OWNER_USER" -g "$OWNER_USER" -m 0644 \
  "$REPO_DIR/configs/xrdp/xsession" "$home/.xsession"

systemctl enable xrdp
systemctl restart xrdp
