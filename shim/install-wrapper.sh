#!/usr/bin/env bash
# Временная установка обёртки, которая запускает игру с шимом.
#
#   shim/install-wrapper.sh --enable    # включить (оригинал остаётся рядом)
#   shim/install-wrapper.sh --restore   # вернуть оригинальный бинарник
#   shim/install-wrapper.sh --status     # что сейчас
#
# Зачем обёртка: deep-link (share.polytopia.io → opengame?id=…) игра получает
# только при запуске из Steam, а строку запуска Steam на ходу не перечитывает.
# Обёртка — обратимая замена до появления установщика (веха 6.4).
set -euo pipefail

GAME="${POLYTOPIA_DIR:-$HOME/.steam/steam/steamapps/common/The Battle of Polytopia}"
BIN="$GAME/Polytopia.x86_64"
REAL="$GAME/Polytopia.x86_64.real"
SHIM="${POLYREAD_SHIM:-$HOME/.local/share/polytopia-analyzer/bin/libpolyread_dump.so}"
OUT="${POLYREAD_DUMP_DIR:-$HOME/.local/share/polytopia-analyzer/inbox}"
ORIG_MD5=71d3c9d3d6ab51213823eadb1a4b9dcd

die() { echo "$*" >&2; exit 1; }

[ -d "$GAME" ] || die "игра не найдена: $GAME (задайте POLYTOPIA_DIR)"

case "${1:---status}" in
--enable)
    [ -f "$SHIM" ] || die "нет шима: $SHIM (соберите: make -C shim и скопируйте в ~/.local/share/polytopia-analyzer/bin/)"
    if [ -f "$REAL" ]; then
        echo "уже включено (есть $REAL)"
    else
        [ -x "$BIN" ] || die "нет бинарника: $BIN"
        cp -p "$BIN" "$REAL"
        cat > "$BIN" <<WRAP
#!/bin/sh
# Обёртка polyread (временная). Оригинал: Polytopia.x86_64.real
SHIM="$SHIM"
OUT="$OUT"
if [ -n "\$LD_PRELOAD" ]; then LD_PRELOAD="\$SHIM:\$LD_PRELOAD"; else LD_PRELOAD="\$SHIM"; fi
export LD_PRELOAD
POLYREAD_DUMP="\$OUT"; export POLYREAD_DUMP
exec "\$(dirname "\$0")/Polytopia.x86_64.real" "\$@"
WRAP
        chmod 755 "$BIN"
        echo "включено: $BIN → обёртка, оригинал $REAL"
    fi
    # Unity ищет данные рядом с бинарником по его имени
    for l in "Polytopia.x86_64_Data" "Polytopia.x86_64.real_Data"; do
        [ -e "$GAME/$l" ] || ln -s Polytopia_Data "$GAME/$l"
    done
    echo "снимки: $OUT"
    ;;
--restore)
    if [ -f "$REAL" ]; then
        rm -f "$BIN"
        mv "$REAL" "$BIN"
        echo "оригинал возвращён"
    else
        echo "нечего возвращать: $REAL отсутствует"
    fi
    for l in "Polytopia.x86_64_Data" "Polytopia.x86_64.real_Data"; do
        [ -L "$GAME/$l" ] && rm -f "$GAME/$l"
    done
    md5=$(md5sum "$BIN" | cut -d' ' -f1)
    echo "md5: $md5"
    [ "$md5" = "$ORIG_MD5" ] && echo "контрольная сумма совпадает с оригиналом" || echo "ВНИМАНИЕ: md5 не совпадает с ожидаемым"
    ;;
--status)
    if [ -f "$REAL" ]; then echo "состояние: включено (обёртка)"; else echo "состояние: оригинал на месте"; fi
    md5sum "$BIN" 2>/dev/null || true
    ;;
*)
    die "использование: $0 [--enable|--restore|--status]"
    ;;
esac