#!/bin/sh
# Chrome с отдельным постоянным профилем. Второй профиль Chrome сюда не добавлять.
PROFILE_DIR="$HOME/tiktok-profile"
mkdir -p "$PROFILE_DIR"
exec google-chrome-stable --user-data-dir="$PROFILE_DIR" --no-first-run --no-default-browser-check https://www.tiktok.com
