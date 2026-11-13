#!/usr/bin/env bash
# Сборка релиза для игрока: один исполняемый файл + установщик + ярлык.
#
#   scripts/build_release.sh              собрать в dist/
#   scripts/build_release.sh --no-shim    без шима (сборка без Linux-игр)
#
# Результат:
#   dist/polytopia-analyzer-<версия>-linux-x86_64/
#       polytopia-analyzer      один файл, ничего ставить не нужно
#       install.sh              ставит в ~/.local и создаёт ярлык в меню
#       polytopia-analyzer.desktop.in, icon.svg
#       README.md               инструкция для игрока
#   dist/polytopia-analyzer-<версия>-linux-x86_64.tar.gz + .sha256
set -euo pipefail

cd "$(dirname "$0")/.."
PY=.venv/bin/python
VERSION=$("$PY" -c "import polytopia_analyzer; print(polytopia_analyzer.__version__)")
NAME="polytopia-analyzer-$VERSION-linux-x86_64"
BUILD_SHIM=1
[ "${1:-}" = "--no-shim" ] && BUILD_SHIM=0

echo "== версия: $VERSION"

if [ "$BUILD_SHIM" -eq 1 ]; then
    echo "== шим"
    make -C shim >/dev/null
    mkdir -p polytopia_analyzer/assets/bin
    cp -f shim/libpolyread_dump.so polytopia_analyzer/assets/bin/
    echo "   polytopia_analyzer/assets/bin/libpolyread_dump.so"
fi

echo "== onefile"
rm -rf build dist
"$PY" -m PyInstaller --noconfirm --clean --onefile \
    --name polytopia-analyzer \
    --paths . \
    --add-data "polytopia_analyzer/assets:polytopia_analyzer/assets" \
    --collect-submodules polytopia_analyzer \
    packaging/entry.py >/tmp/pyinstaller.log 2>&1 || {
        echo "Сборка не удалась, лог: /tmp/pyinstaller.log"; tail -20 /tmp/pyinstaller.log; exit 1; }

OUT="dist/$NAME"
mkdir -p "$OUT"
cp dist/polytopia-analyzer "$OUT/polytopia-analyzer"
cp packaging/install.sh packaging/polytopia-analyzer.desktop.in packaging/icon.svg "$OUT/"
cp docs/PLAYER.md "$OUT/README.md"
chmod +x "$OUT/polytopia-analyzer" "$OUT/install.sh"

echo "== архив"
tar -C dist -czf "dist/$NAME.tar.gz" "$NAME"
( cd dist && sha256sum "$NAME.tar.gz" > "$NAME.tar.gz.sha256" )

SIZE=$(du -h "$OUT/polytopia-analyzer" | cut -f1)
ARCH=$(du -h "dist/$NAME.tar.gz" | cut -f1)
echo
echo "Готово:"
echo "  бинарник: dist/$NAME/polytopia-analyzer ($SIZE)"
echo "  архив:    dist/$NAME.tar.gz ($ARCH)"
echo "  сумма:    dist/$NAME.tar.gz.sha256"
echo
echo "Проверка бинарника: $OUT/polytopia-analyzer --version"