#!/usr/bin/env bash
# Веха 6.2: запуск игры с дампом состояния матча.
#
#   make -C shim
#   shim/run-dump.sh            # снимки по ходам в ~/.local/share/polytopia-analyzer/inbox/
#
# Снимки: snapshot-<ход>.json + latest.json, журнал — dump.log.
# Дамп сам подхватывает состояние, когда открыт реплей/матч; в меню объекта нет.
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
GAME="${POLYTOPIA_DIR:-$HOME/.steam/steam/steamapps/common/The Battle of Polytopia}"
SO="$HERE/libpolyread_dump.so"
OUT="${POLYREAD_DUMP_DIR:-$HOME/.local/share/polytopia-analyzer/inbox}"
APPID=874390

[ -f "$SO" ] || { echo "Сначала соберите: make -C $HERE" >&2; exit 1; }
[ -x "$GAME/Polytopia.x86_64" ] || { echo "Игра не найдена: $GAME (задайте POLYTOPIA_DIR)" >&2; exit 1; }

mkdir -p "$OUT"
echo "игра:     $GAME"
echo "дамп:     $SO"
echo "снимки:   $OUT"
echo

cd "$GAME"
POLYREAD_DUMP="$OUT" \
SteamAppId="$APPID" SteamGameId="$APPID" SteamClientLaunch=1 \
LD_PRELOAD="$SO" \
  ./Polytopia.x86_64 "$@"