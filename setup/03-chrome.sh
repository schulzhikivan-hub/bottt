#!/usr/bin/env bash
# Google Chrome и ярлык с постоянным профилем TikTok.
set -euo pipefail

source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"
require_root

log "Google Chrome"
if ! command -v google-chrome-stable >/dev/null 2>&1; then
  tmp="$(mktemp -d)"
  trap 'rm -rf "$tmp"' EXIT
  wget -q -O "$tmp/chrome.deb" \
    https://dl.google.com/linux/direct/google-chrome-stable_current_amd64.deb
  DEBIAN_FRONTEND=noninteractive apt-get install -y "$tmp/chrome.deb"
fi

log "Ярлык и профиль TikTok"
home="$(getent passwd "$OWNER_USER" | cut -d: -f6)"
as_owner mkdir -p "$home/.local/bin" "$home/.local/share/applications" "$home/.config" "$home/tiktok-profile"

log "Прокси для Chrome: ${CHROME_PROXY:-не задан}"
if [ -n "$CHROME_PROXY" ] && ! [[ "$CHROME_PROXY" =~ ^(http|socks5)://[A-Za-z0-9.-]+:[0-9]+$ ]]; then
  die "CHROME_PROXY должен быть вида http://host:port или socks5://host:port, без логина и пароля"
fi
proxy_tmp="$(mktemp)"
printf 'CHROME_PROXY=%s\n' "$CHROME_PROXY" > "$proxy_tmp"
install -o "$OWNER_USER" -g "$OWNER_USER" -m 0600 "$proxy_tmp" "$home/.config/tiktok-chrome.env"
rm -f "$proxy_tmp"

install -o "$OWNER_USER" -g "$OWNER_USER" -m 0755 \
  "$REPO_DIR/configs/chrome/tiktok-chrome.sh" "$home/.local/bin/tiktok-chrome.sh"

# В .desktop нельзя использовать $HOME, поэтому подставляем домашний каталог явно.
desktop_tmp="$(mktemp)"
sed "s|__HOME__|$home|g" "$REPO_DIR/configs/chrome/tiktok-chrome.desktop" > "$desktop_tmp"
install -o "$OWNER_USER" -g "$OWNER_USER" -m 0644 \
  "$desktop_tmp" "$home/.local/share/applications/tiktok-chrome.desktop"
rm -f "$desktop_tmp"
