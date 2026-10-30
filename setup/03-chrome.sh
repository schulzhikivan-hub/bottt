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
as_owner mkdir -p "$home/.local/bin" "$home/.local/share/applications" "$home/tiktok-profile"

install -o "$OWNER_USER" -g "$OWNER_USER" -m 0755 \
  "$REPO_DIR/configs/chrome/tiktok-chrome.sh" "$home/.local/bin/tiktok-chrome.sh"

# В .desktop нельзя использовать $HOME, поэтому подставляем домашний каталог явно.
desktop_tmp="$(mktemp)"
sed "s|__HOME__|$home|g" "$REPO_DIR/configs/chrome/tiktok-chrome.desktop" > "$desktop_tmp"
install -o "$OWNER_USER" -g "$OWNER_USER" -m 0644 \
  "$desktop_tmp" "$home/.local/share/applications/tiktok-chrome.desktop"
rm -f "$desktop_tmp"
