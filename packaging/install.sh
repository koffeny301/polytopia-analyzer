#!/bin/sh
# Установка Polytopia Analyzer для текущего пользователя (без прав root).
#
#   ./install.sh              установить в ~/.local
#   ./install.sh --uninstall  удалить
#   PREFIX=/opt/foo ./install.sh
#
# После установки в меню приложений появляется «Анализатор матчей Polytopia».
set -eu

HERE=$(cd "$(dirname "$0")" && pwd)
PREFIX=${PREFIX:-$HOME/.local}
BIN_DIR="$PREFIX/bin"
APPS_DIR="$PREFIX/share/applications"
ICON_DIR="$PREFIX/share/icons/hicolor/scalable/apps"
BIN="$BIN_DIR/polytopia-analyzer"
DESKTOP="$APPS_DIR/polytopia-analyzer.desktop"
ICON="$ICON_DIR/polytopia-analyzer.svg"

say() { printf '%s\n' "$*"; }

if [ "${1:-}" = "--uninstall" ]; then
    rm -f "$BIN" "$DESKTOP" "$ICON"
    command -v update-desktop-database >/dev/null 2>&1 && update-desktop-database "$APPS_DIR" 2>/dev/null || true
    say "Удалено: $BIN, $DESKTOP, $ICON"
    say "Данные (матчи, снимки, настройки) не тронуты: ~/.local/share/polytopia-analyzer"
    exit 0
fi

[ -f "$HERE/polytopia-analyzer" ] || { say "Ошибка: рядом нет файла polytopia-analyzer"; exit 1; }
[ -f "$HERE/polytopia-analyzer.desktop.in" ] || { say "Ошибка: нет polytopia-analyzer.desktop.in"; exit 1; }

mkdir -p "$BIN_DIR" "$APPS_DIR" "$ICON_DIR"
install -m 755 "$HERE/polytopia-analyzer" "$BIN"
if [ -f "$HERE/icon.svg" ]; then
    install -m 644 "$HERE/icon.svg" "$ICON"
    icon_name="polytopia-analyzer"
else
    icon_name="applications-games"
fi

sed -e "s|@EXEC@|$BIN|g" -e "s|@ICON@|$icon_name|g" \
    "$HERE/polytopia-analyzer.desktop.in" > "$DESKTOP"
chmod 644 "$DESKTOP"

command -v update-desktop-database >/dev/null 2>&1 && update-desktop-database "$APPS_DIR" 2>/dev/null || true

say "Установлено:"
say "  программа: $BIN"
say "  ярлык:     $DESKTOP"
say ""
say "Дальше:"
say "  1. Запустите «Анализатор матчей Polytopia» из меню приложений —"
say "     откроется браузер с локальной страницей."
say "  2. В карточке «Чтение из игры (Steam)» нажмите «Включить»."
say "  3. Откройте реплей по ссылке share.polytopia.io — матч появится сам."
say ""
say "Удалить: $HERE/install.sh --uninstall"