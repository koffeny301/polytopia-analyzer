# Шим: чтение состояния Polytopia изнутри игры

Две `.so`, обе подгружаются через `LD_PRELOAD` и работают только на чтение:
ничего не пишут в игру, не вызывают её методов.

| Файл | Веха | Что делает |
|---|---|---|
| `libpolyread_probe.so` | 6.1 | перечисляет сборки, классы и поля (`POLYREAD_PROBE=1`) |
| `libpolyread_dump.so` | 6.2 | читает живой `GameState` и пишет снимки по ходам (`POLYREAD_DUMP=1`) |

```bash
make                        # обе библиотеки
./run-probe.sh              # метаданные → ~/.local/share/polytopia-analyzer/probe/

# штатный путь: строка запуска Steam (веха 6.4)
polytopia-analyzer install-shim enable --restart-steam
xdg-open 'steam://run/874390//opengame?id=<uuid>'   # реплей из ссылки
polytopia-analyzer install-shim disable --restart-steam

# резервный путь, если менять строку запуска нельзя
./install-wrapper.sh --enable   # … --restore
```

Дамп пишет `snapshot-<ход>.json` + `latest.json` в
`~/.local/share/polytopia-analyzer/inbox/`. Подробности и результаты —
`docs/shim-dump.md`.

### Где живёт состояние

`GameManager.instance.client.currentGameState` → `<CurrentTurn>`, `<PlayerStates>`
(`score`, `currency`, `cities`, `kills`, `casualities`, `resignedTurn`, `tribe`).
Поля ищутся по цепочке наследования: у `ReplayClient` своих полей нет.

## Переменные окружения

| Переменная | Смысл |
|---|---|
| `POLYREAD_PROBE=1` или `=/путь` | включает пробник (без неё `.so` ничего не делает) |
| `POLYREAD_PROBE_DELAY=6` | пауза после загрузки движка, секунды |
| `POLYREAD_PROBE_ATTACH=1` | вызвать `il2cpp_thread_attach` (в пробнике по умолчанию нет) |
| `POLYREAD_PROBE_FORK=1` | опрос из дочернего процесса (не работает, оставлено для диагностики) |
| `POLYREAD_PROBE_FORCE=1` | игнорировать проверку имени исполняемого файла |
| `POLYREAD_DUMP=1` или `=/путь` | включает дамп состояния (6.2) |
| — | **пишет только в реплее**: клиент игры должен быть `ReplayClient`, иначе снимков нет |
| `POLYREAD_DUMP_ALLOW_LIVE=1` | снять фильтр «только реплей» (отладка; в живом матче не использовать) |
| `POLYREAD_DUMP_REQUIRE=<класс>` | какой класс клиента считать реплеем (по умолчанию `ReplayClient`) |
| `POLYREAD_DUMP_NO_ATTACH=1` | не регистрировать поток в IL2CPP (по умолчанию регистрируем — иначе GC роняет игру) |
| `POLYREAD_DUMP_FIND=1` | только найти `GameState`, без снимков |
| `POLYREAD_DUMP_SWEEP=1` | диагностический смотр объектного графа |
| `POLYREAD_DUMP_FIELDS="A,B"` | напечатать поля указанных классов |
| `POLYREAD_DUMP_WATCH=1` | 12 тактов наблюдения с журналом |
| `POLYREAD_DUMP_DELAY=6` | пауза после загрузки движка, секунды |
| `POLYREAD_DUMP_INTERVAL=300` | период опроса, мс |
| `POLYTOPIA_DIR`, `POLYREAD_PROBE_DIR`, `POLYREAD_DUMP_DIR` | путь к игре / куда писать |

## Грабли, проверенные на 2.18.0.16751

1. Вызов любого `il2cpp_*` во время `il2cpp_init` роняет игру:
   `Exclusion ranges overlap` → SIGABRT внутри `il2cpp_init`. Поэтому пауза
   (6 с) после загрузки `GameAssembly.so` и никаких опросов на старте.
2. `free()` на результат `il2cpp_type_get_name()` роняет процесс
   (`free(): invalid size`). Не освобождаем ничего из отданного движком.
3. `il2cpp_thread_attach()` из собственного потока падает в `abort()`
   (Boehm GC). Для чтения имён и типов полей он не нужен — не вызываем.
4. Опрос из `fork`-ребёнка невозможен: копия процесса с мёртвыми потоками
   валится на первом же вызове API (4 попытки из 4 — SIGABRT).
5. Запуск мимо Steam: бинарник просит Steam перезапустить игру, и настоящий
   процесс стартует без `LD_PRELOAD`. Лечится `SteamAppId=874390
   SteamGameId=874390 SteamClientLaunch=1` (см. `run-probe.sh`) либо строкой
   запуска в Steam — это и будет механизм 6.4.
6. `il2cpp_class_from_name()` находит не всё (например, `Player` в
   `PolytopiaAssembly.dll` не нашёлся) → ищем перебором классов образа.