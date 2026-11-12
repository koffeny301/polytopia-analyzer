#!/usr/bin/env bash
# Запуск игры с пробником 6.1 (нативная Linux-сборка).
#
#   make -C shim          # собрать libpolyread_probe.so
#   shim/run-probe.sh     # запустить игру и снять метаданные
#
# Результат: $POLYREAD_PROBE_DIR (по умолчанию ~/.local/share/polytopia-analyzer/probe/)
#   probe.json    — сборки, классы, поля ключевых классов
#   probe.log     — то же человекочитаемо
#   classes.txt   — все 17 тыс. классов (сборка, namespace, имя)
#   supervisor.log/trace.log — служебные
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
GAME="${POLYTOPIA_DIR:-$HOME/.steam/steam/steamapps/common/The Battle of Polytopia}"
SO="$HERE/libpolyread_probe.so"
OUT="${POLYREAD_PROBE_DIR:-$HOME/.local/share/polytopia-analyzer/probe}"
APPID=874390

[ -f "$SO" ] || { echo "Сначала соберите: make -C $HERE" >&2; exit 1; }
[ -x "$GAME/Polytopia.x86_64" ] || { echo "Игра не найдена: $GAME (задайте POLYTOPIA_DIR)" >&2; exit 1; }

mkdir -p "$OUT"
echo "игра:     $GAME"
echo "пробник:  $SO"
echo "выгрузка: $OUT"
echo

# SteamAppId/SteamGameId/SteamClientLaunch обязательны: без них бинарник
# просит Steam перезапустить игру, и настоящий процесс стартует уже без
# LD_PRELOAD (Steam передаёт своё окружение, а не наше).
cd "$GAME"
POLYREAD_PROBE="$OUT" \
SteamAppId="$APPID" SteamGameId="$APPID" SteamClientLaunch=1 \
LD_PRELOAD="$SO" \
  ./Polytopia.x86_64 "$@"