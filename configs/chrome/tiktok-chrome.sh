#!/bin/sh
# Chrome с отдельным постоянным профилем. Второй профиль Chrome сюда не добавлять.
# Прокси берётся из ~/.config/tiktok-chrome.env (его пишет setup/03-chrome.sh из settings.env)
# и действует только на этот Chrome, а не на всю систему.
PROFILE_DIR="$HOME/tiktok-profile"
CHROME_PROXY=""
[ -f "$HOME/.config/tiktok-chrome.env" ] && . "$HOME/.config/tiktok-chrome.env"
mkdir -p "$PROFILE_DIR"

if [ -n "$CHROME_PROXY" ]; then
  # WebRTC тоже через прокси, иначе он может показать IP самого VPS.
  exec google-chrome-stable --user-data-dir="$PROFILE_DIR" --no-first-run --no-default-browser-check \
    --proxy-server="$CHROME_PROXY" \
    --force-webrtc-ip-handling-policy=disable_non_proxied_udp \
    https://www.tiktok.com
fi

exec google-chrome-stable --user-data-dir="$PROFILE_DIR" --no-first-run --no-default-browser-check https://www.tiktok.com
