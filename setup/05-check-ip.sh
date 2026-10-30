#!/usr/bin/env bash
# Запускать от владельца, не от root.
# Записывает текущий IP и геоданные в ~/ip-log.txt, чтобы видеть, меняется ли IP.
set -euo pipefail

info="$(curl -fsS https://ipinfo.io/json | tr -d '\n ')"
printf '%s %s\n' "$(date -Is)" "$info" | tee -a "$HOME/ip-log.txt"
