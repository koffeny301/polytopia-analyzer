#!/usr/bin/env bash
# Диагностика установки PolyMod для The Battle of Polytopia
# (Linux: нативная сборка IL2CPP, Proton, Windows-депо).
# Запуск: bash scripts/diagnose_polymod.sh
set -u

APPID=874390
GAME_NAME="The Battle of Polytopia"

ok()   { echo "  ✅ $1"; }
warn() { echo "  ⚠️  $1"; }
fail() { echo "  ❌ $1"; }

steam_roots() {
    local roots=()
    for d in "$HOME/.steam/steam" "$HOME/.local/share/Steam" "$HOME/Steam"; do
        [ -d "$d" ] && roots+=("$(readlink -f "$d")")
    done
    local extra=()
    for r in "${roots[@]}"; do
        local vdf="$r/steamapps/libraryfolders.vdf"
        [ -f "$vdf" ] || continue
        while IFS= read -r p; do
            [ -d "$p" ] && extra+=("$(readlink -f "$p")")
        done < <(grep -oE '"path"[[:space:]]+"[^"]+"' "$vdf" | sed -E 's/.*"([^"]+)"$/\1/')
    done
    printf '%s\n' "${roots[@]}" "${extra[@]}" 2>/dev/null | sort -u
}

echo "== Диагностика PolyMod (The Battle of Polytopia, AppID $APPID) =="
echo

found=0
while IFS= read -r root; do
    [ -n "$root" ] || continue
    gamedir="$root/steamapps/common/$GAME_NAME"
    [ -d "$gamedir" ] || continue
    found=1
    echo "Каталог игры: $gamedir"

    # версия игры
    if [ -f "$gamedir/version.txt" ]; then
        ok "версия игры: $(head -1 "$gamedir/version.txt")"
    fi

    # тип сборки
    if [ -f "$gamedir/Polytopia.x86_64" ]; then
        ok "нативная Linux-сборка (Polytopia.x86_64)"
        if [ -f "$gamedir/GameAssembly.so" ]; then
            ok "движок IL2CPP (GameAssembly.so) → нужен BepInEx linux-il2cpp-x64"
        fi
    elif [ -f "$gamedir/Polytopia.exe" ]; then
        ok "Windows-сборка (Polytopia.exe) → Proton-сценарий: BepInEx win-x64 + winhttp.dll"
        if [ -f "$gamedir/winhttp.dll" ]; then
            ok "winhttp.dll (doorstop-прокси) на месте"
        else
            warn "winhttp.dll не найден рядом с Polytopia.exe — BepInEx не загрузится"
        fi
    else
        fail "ни Polytopia.x86_64, ни Polytopia.exe не найдены — установка повреждена?"
    fi

    # BepInEx
    if [ -d "$gamedir/BepInEx" ]; then
        ok "каталог BepInEx/ существует"
        if ls "$gamedir"/BepInEx/core/* >/dev/null 2>&1; then
            ok "BepInEx/core: $(ls "$gamedir"/BepInEx/core | head -3 | tr '\n' ' ')"
        else
            fail "BepInEx/core пуст — ядро BepInEx не установлено"
        fi
        if ls "$gamedir"/BepInEx/plugins/* >/dev/null 2>&1; then
            ok "BepInEx/plugins: $(ls "$gamedir"/BepInEx/plugins | tr '\n' ' ')"
        else
            fail "BepInEx/plugins пуст — положите туда PolyMod.dll"
        fi
        log="$gamedir/BepInEx/LogOutput.log"
        if [ -f "$log" ]; then
            ok "LogOutput.log найден ($(wc -l < "$log") строк)"
            if grep -qi "polymod" "$log"; then
                ok "в логе упоминается PolyMod"
            else
                warn "PolyMod в логе НЕ упоминается — загрузчик не подхватился"
            fi
            echo "  --- последние строки лога ---"
            tail -n 5 "$log" | sed 's/^/  | /'
            echo "  -----------------------------"
        else
            warn "LogOutput.log нет — игра с BepInEx ещё не запускалась (или BepInEx не загрузился)"
        fi
    else
        fail "BepInEx не установлен в каталоге игры (см. docs/polymod-setup.md, шаг A2)"
    fi

    # Mods
    if [ -d "$gamedir/Mods" ]; then
        if ls "$gamedir"/Mods/* >/dev/null 2>&1; then
            ok "Mods/: $(ls "$gamedir"/Mods | tr '\n' ' ')"
        else
            warn "Mods/ существует, но пуст — положите туда мод-захватчик (.polymod/.dll)"
        fi
    else
        warn "каталога Mods/ нет — создайте и положите мод-захватчик"
    fi

    # Proton prefix
    prefix="$root/steamapps/compatdata/$APPID"
    if [ -d "$prefix/pfx" ]; then
        ok "Proton-префикс существует: $prefix/pfx"
    elif [ -d "$prefix" ]; then
        warn "compatdata/$APPID есть, но pfx пуст — Proton фактически не использовался (играется нативная сборка)"
    fi
    echo
done < <(steam_roots)

if [ "$found" -eq 0 ]; then
    fail "Каталог игры '$GAME_NAME' не найден ни в одной библиотеке Steam."
    echo "   Проверьте: Steam → библиотека → игра → свойства → установленные файлы."
    exit 1
fi

echo "Что прислать разработчику анализатора:"
echo "  1) вывод этого скрипта;"
echo "  2) <каталог игры>/BepInEx/LogOutput.log;"
echo "  3) файлы экспорта из <каталог игры>/Mods/ (.json / .polyreplay), если есть."
