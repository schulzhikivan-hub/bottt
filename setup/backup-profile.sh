#!/usr/bin/env bash
# Запускать от владельца, не от root.
# Закрывает Chrome с этим профилем и создаёт зашифрованный архив (спросит пароль).
set -euo pipefail

profile="$HOME/tiktok-profile"
backups="$HOME/backups"

[ -d "$profile" ] || { echo "Профиль не найден: $profile" >&2; exit 1; }
mkdir -p "$backups"

# Chrome держит базу cookies открытой, поэтому сначала закрываем его.
pkill -f "user-data-dir=$profile" || true
while pgrep -f "user-data-dir=$profile" >/dev/null; do
  sleep 1
done

archive="$backups/tiktok-profile-$(date +%F-%H%M).tar.gz.gpg"
tar czf - -C "$HOME" tiktok-profile | gpg --symmetric --cipher-algo AES256 -o "$archive"
echo "Готово: $archive"
