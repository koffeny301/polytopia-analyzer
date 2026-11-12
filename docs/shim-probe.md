# Результат вехи 6.1 — пробник шима (чтение метаданных IL2CPP)

Дата: 2026-11-13. Стенд: та же машина, игра **The Battle of Polytopia 2.18.0.16751**,
нативная Linux-сборка, `GameAssembly.so` 107 МБ (md5 `c80623b9fb9160ef6073b616f60774e1`).

Гипотеза вехи: изнутри процесса игры через штатный IL2CPP API можно перечислить
классы и поля и найти объекты состояния. **Гипотеза подтверждена.**

## Что получилось (реальный прогон)

| Показатель | Значение |
|---|---|
| Функций `il2cpp_*` в экспорте `GameAssembly.so` | 241 |
| Сборок в рантайме | **146** |
| Классов всего | **17 549** |
| Ключевых классов найдено | **8 из 15** |
| Время | движок загрузился через 0.3 с, выгрузка на 6-й секунде, вся работа < 1 с |
| Состояние игры после прогона | живая, главное меню, без ошибок |

Игровые сборки: `PolytopiaAssembly.dll` (1911 классов), `GameLogicAssembly.dll` (433),
`PolytopiaBackendBase.dll` (649). Состояние матча живёт в `GameLogicAssembly.dll`.

## Найденные классы и поля

| Класс | Сборка | Полей | Что внутри (выборка) |
|---|---|---|---|
| `GameManager` | PolytopiaAssembly | 49 (13 статических) | **`static GameManager instance`** — точка входа в живое состояние |
| `GameState` | GameLogicAssembly | 18 | `CurrentTurn` (UInt32), `CurrentPlayerIndex` (Byte), `Map` (MapData), `PlayerStates` (List<PlayerState>), `Settings` (GameSettings), `CurrentUnitId`, `CommandStack`, `ActionStack` |
| `PlayerState` | GameLogicAssembly | 38 (2 статических) | `score` (UInt32), `currency` (Int32, звёзды), `cities` (Int32), `kills`, `casualities`, `tribe` (TribeType), `resignedTurn`, `wipedAtCommandIndex`, `availableTech`, `relations` |
| `UnitState` | GameLogicAssembly | 22 | `id`, `owner` (Byte), `type` (Polytopia.Data.UnitData.Type), `coordinates` (WorldCoordinates), `health` (UInt16), `promotionLevel`, `xp`, `passengerUnit` |
| `Unit` | PolytopiaAssembly | 19 | рендерер юнита: `unitData`, `unitState`, спрайты/твины |
| `City` | PolytopiaAssembly | 4 | рендерер города (`cityRenderer`, `depth`, `cityOverlay`, `info`) — это **не** данные |
| `MapData` | GameLogicAssembly | 4 | карта |
| `Player` | PolytopiaAssembly | 4 | UI-класс, не данные |

Не нашлись (в этих сборках нет такого имени): `Tribe`, `GameData`, `GameStateData`,
`CityState`, `Board`, `MatchState`, `ReplayData`. Данные города, судя по полям
`Unit.unitData`/`UnitState.type`, лежат в `Polytopia.Data` (`GameLogicAssembly`,
namespace `Polytopia.Data`), искать надо там — это работа 6.2.

Важное для метрик: **счёт, звёзды, города и убийства есть прямо в `PlayerState`**
(`score`, `currency`, `cities`, `kills`, `casualities`). Доход отдельным полем не
хранится — его придётся считать (как и планировалось в `importers/differ.py`),
либо брать из истории `currency` по ходам.

Выгрузка полного списка: `~/.local/share/polytopia-analyzer/probe/classes.txt`
(17 549 строк «сборка → namespace → имя»).

## Что это значит для 6.2

`GameManager.instance` — статическое поле, то есть живой объект состояния
достаётся без обхода сцены: `il2cpp_field_static_get_value(GameManager, instance)`
→ `GameState` → `PlayerStates` / `Map`. Дальше остаётся только разобрать типы
(списки, вложенные структуры) и собрать JSON по ходам.

## Грабли (все воспроизведены)

1. **`il2cpp_*` во время `il2cpp_init`** роняет игру: `Exclusion ranges overlap`
   → SIGABRT в `il2cpp_init`. Вывод: не опрашивать движок на старте; ждать
   загрузки `GameAssembly.so` и выдерживать паузу.
2. **`free()` на `il2cpp_type_get_name()`** роняет процесс (`free(): invalid size`).
   Результаты движка не освобождаем.
3. **`il2cpp_thread_attach()`** из своего потока падает в `abort()` (Boehm GC).
   Для чтения метаданных не нужен — не вызываем.
4. **Изоляция через `fork` не работает**: ребёнок наследует GC с потоками,
   которых в копии нет, и валится на первом вызове API (4 попытки из 4 — SIGABRT).
   Значит шим обязан быть аккуратным, а не «одноразово падающим».
5. **Запуск мимо Steam**: бинарник вызывает `SteamAPI_RestartAppIfNecessary`,
   Steam перезапускает игру, и настоящий процесс стартует уже без `LD_PRELOAD`.
   Рабочий обход для тестов: `SteamAppId=874390 SteamGameId=874390
   SteamClientLaunch=1`; для игрока — строка запуска Steam (механизм 6.4).
6. **`il2cpp_class_from_name()` находит не всё** (`Player` не нашёлся при
   наличии класса) → поиск по имени перебором классов образа.

## Как повторить

```bash
make -C shim
shim/run-probe.sh
cat ~/.local/share/polytopia-analyzer/probe/probe.log | head -40
```

Подробности по переменным окружения — `shim/README.md`.