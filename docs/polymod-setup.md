# Настройка PolyMod для автоэкспорта данных реплея (спайк M0)

Цель спайка: получить **один реальный файл экспорта** игровых данных из
реплея Polytopia и понять, работает ли связка на вашей платформе. По образцу
экспорта будет написан адаптер `importers/polymod.py` (веха M3), и конвейер
станет полностью автоматическим:

```
ссылка на матч → polytopia-analyzer open --launch → игра показывает реплей
   → мод записывает состояния игры в файл → import → отчёт
```

## Факты об этой машине (проверено diagnose_polymod.sh, 2026-11-12)

- Игра установлена в ДВУХ библиотеках Steam:
  `~/.local/share/Steam/...` и `/mnt/disk2/SteamLibrary/...` (одинаковый build);
- сборка **нативная Linux** (`Polytopia.x86_64`, `UnityPlayer.so`),
  движок **IL2CPP** (`GameAssembly.so`) — это НЕ Proton;
- версия игры: **2.18.0.16751**;
- `compatdata/874390` существует, но пуст — Proton-префикса нет, Proton
  фактически не используется (Steam запускает нативную версию).

Следствие: основной сценарий — **вариант A (нативный Linux + BepInEx
linux-il2cpp)**. Варианты B (Proton) и C (Windows) — запасные.

Статус PolyMod (официальный мод-лоадер, https://polymod.dev,
GitHub: PolyModdingTeam/PolyMod, актуальная версия v1.2.19):

| Платформа | Поддержка |
|---|---|
| Windows x86 | ✅ официально |
| Linux | ⏺️ экспериментально |
| macOS | ⏺️ экспериментально |

---

## Вариант A — нативный Linux (основной)

### A1. BepInEx для Linux IL2CPP

1. Скачайте **BepInEx 6 (bleeding edge) linux-il2cpp-x64** с
   https://builds.bepinex.dev/projects/bepinex-be (точная сборка — по
   инструкции polymod.dev → Wiki; версия BepInEx должна соответствовать
   тому, что ожидает PolyMod).
2. Распакуйте архив В каталог игры, чтобы `BepInEx/` лежал рядом с
   `Polytopia.x86_64`:
   ```
   <Steam>/steamapps/common/The Battle of Polytopia/
   ├── BepInEx/
   ├── Polytopia.x86_64
   └── ...
   ```
3. Первый запуск: в свойствах игры в Steam укажите опцию запуска по
   инструкции BepInEx для Linux (обычно это `LD_PRELOAD`/doorstop-обвязка
   из архива или запуск `Polytopia.x86_64` один раз из терминала для
   генерации конфигурации). Проверьте появление `BepInEx/LogOutput.log`.

### A2. PolyMod

1. Скачайте `PolyMod.dll` из релизов:
   https://github.com/PolyModdingTeam/PolyMod/releases (v1.2.19) или
   установщик с https://polymod.dev (распаковать 7z, не запуская).
2. Положите `PolyMod.dll` в `BepInEx/plugins/`.
3. Создайте в каталоге игры папку `Mods/`.
4. Запустите игру, выйдите, проверьте лог:
   `grep -i polymod "BepInEx/LogOutput.log"` — должны быть строки PolyMod.
   Автоматически всё это проверяет `scripts/diagnose_polymod.sh`.

### A3. Мод-захватчик данных (кандидаты)

| Мод | Где взять | Что делает | Ограничения |
|---|---|---|---|
| **polyfish-mod** | github.com/HenBOMB/Polyfish → `polyfish-mod/` | автопроигрывание реплея (×20), отправка состояний игры на локальный сервер | нужен принимающий сервер на localhost:3000 (Polyfish или наша заглушка); поддерживает мультиплеерные реплеи |
| **polytopia-singleplayer-library** | github.com/xsmfder/polytopia-singleplayer-library → Releases (`single_player_library-0.1.7.polymod`) | многоarchive одиночной игры + локальные реплеи `.polyreplay` (файл можно скопировать) | только одиночные игры; проверен на игре **2.17.3.16375** + PolyMod 1.2.18 и **отклоняет другие версии** — с нашей 2.18.0.16751 может не запуститься |

Установка: `.polymod`/`.dll` мода — в папку `Mods/` каталога игры.

⚠️ Главный риск спайка: версия игры 2.18.0.16751 новее проверенных модом
2.17.3.16375. Если мод откажется работать — варианты: (1) откатить игру на
старую ветку в Steam (свойства → беты), (2) ждать обновления мода,
(3) перейти к варианту C (Windows-машина).

### A4. Захват реплея

```bash
polytopia-analyzer open "https://share.polytopia.io/g/<uuid>" --launch
```

Досмотрите реплей до конца (или дайте моду автопроиграть его). Файлы экспорта
ищите так (отметьте время перед запуском):

```bash
touch /tmp/mark   # перед открытием реплея
find "<каталог игры>" -newer /tmp/mark \( -name "*.json" -o -name "*.polyreplay" \) 2>/dev/null
```

Для polyfish-mod данные уходят POST-запросами на localhost:3000 — если
сервер Polyfish ставить не хочется, скажите мне: я соберу
мини-приёмник (один stdlib-скрипт, пишет всё полученное в `data/inbox/`).

### A5. Что прислать мне по итогам спайка

1. Файл(ы) экспорта (`.json` / `.polyreplay`) — даже если формат непонятен.
2. `BepInEx/LogOutput.log` из каталога игры.
3. Вывод `scripts/diagnose_polymod.sh`.

---

## Вариант B — принудительный Proton (если нативный BepInEx не заведётся)

1. Steam → игра → Свойства → Совместимость → включить Proton (любая свежая).
2. Steam докачает Windows-депо: в каталоге игры появится `Polytopia.exe`.
3. Далее как в варианте C (BepInEx win-x64 + `winhttp.dll` рядом с exe),
   файлы кладутся в тот же каталог игры.

## Вариант C — Windows-машина

1. Установщик PolyMod с https://polymod.dev — сам найдет игру и поставит
   BepInEx + PolyMod.
2. Мод-захватчик (таблица A3) — в `Mods/` каталога игры
   (`...\steamapps\common\The Battle of Polytopia\Mods`).
3. Открыть реплей (в игре: Multiplayer → Replays, или steam://-ссылкой из
   `polytopia-analyzer open "<ссылка>"`).
4. Забрать файлы экспорта и `BepInEx\LogOutput.log`, передать на Linux-машину
   (анализатор ставить на Windows не нужно — импорт сделаем по файлу).

---

## Диагностика (Linux)

```bash
bash scripts/diagnose_polymod.sh
```

Проверяет во всех библиотеках Steam: каталог игры и его тип (нативный
Linux / Windows), версию игры, BepInEx (core/plugins/лог), упоминание
PolyMod в логе, содержимое `Mods/`, состояние Proton-префикса.
Каждый пункт — ✅ / ⚠️ / ❌ с подсказкой.

## Если спайк провалится

Конвейер не заблокирован: ручной ввод (уже работает, с валидацией) и
OCR скриншотов (веха M4) покрывают задачу полностью. Автоимпорт тогда
отложим до обновления модов под версию игры 2.18+.
