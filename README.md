# Polytopia Match Analyzer

**[English](#english) | [Русский](#русский)**

Match analysis for **The Battle of Polytopia** with a Civilization-style final
report: interactive trend charts, final standings, combat stats, lead changes
and awards — all in one self-contained HTML file.

<a id="english"></a>
## English

```
🏆 Winner: koffeny301 (Vengir) — 3,480 points (lead +412)

 #  Player           Score  Growth  ☆/turn  Kills  Losses   K/D  Capt.cities
 1  koffeny301        3480    +3060     8.4     21       9  2.33            4
 2  Wolf_Star         3068    +2648     7.6     14      15  0.93            2
 3  NovaAi            2740    +2320     7.1      8      17  0.47            1
```

### Where the data comes from

**Important: about match links.** A `https://share.polytopia.io/g/<uuid>` link
is only a deep link into the app: match data lives on Midjiwan's servers and is
downloaded exclusively by the game client. There is no public API for it.
So the workflow is:

1. `polytopia-analyzer open <link> --launch` — the tool extracts the match id
   and opens the replay in the game (Steam/mobile).
2. While watching, you fill in the match table (CSV) — create a template with
   the `template` command.
3. `polytopia-analyzer analyze match.csv` — builds the HTML report.

The replay link is stored in the match metadata and included in the report.

**Semi-automatic: reading the score straight from the replay.**
`scripts/harvest_replay.py` captures frames of the replay autoplay, recognizes
the HUD (`Score`, `Stars (+income)`, `Turn`) and builds two series — yours and
the opponent's. It yields score and income per turn; army, cities, kills and
damage are not shown on the HUD (those need the shim). Requires Linux + KDE
(`spectacle`), `tesseract`, X11.

```bash
polytopia-analyzer open <link> --launch      # the replay opens in the game
.venv/bin/python scripts/harvest_replay.py --seconds 180 --out /tmp/frames
# -> /tmp/frames/series.json
```

**Automatic: reading game state (Linux, native build).** An `LD_PRELOAD` shim
(`shim/`) loads into the game process and reads the live `GameState` — score,
stars, cities, kills and losses, army, city income and city captures per player
per turn (snapshot format v2; the match command log goes to `commands.json`).
Nothing is written to the game: read-only access to metadata and fields.

**Replays only.** Before each write the shim verifies that a replay is open
(client class `ReplayClient`). No snapshots are taken in a live match — the
attempt is refused and logged. This is part of the promise: a tool for reviewing
finished matches, not an in-game assistant.

```bash
make -C shim
polytopia-analyzer install-shim enable --restart-steam   # Steam launch options
polytopia-analyzer open <link> --launch                  # replay opens in Steam
polytopia-analyzer import-inbox --watch                  # match is written as the replay plays
polytopia-analyzer install-shim disable --restart-steam  # rollback
```

`install-shim` writes `LD_PRELOAD=… %command%` into the game's Steam launch
options (with a config backup and rollback). Steam keeps its config in memory,
so the installer stops and restarts it; without `--restart-steam` it refuses to
run while Steam is running. The manual route and the temporary-wrapper route
(`shim/install-wrapper.sh`) are documented in `docs/shim-dump.md`.

In the web UI the same thing is done by the "Import from game (shim)" card.
Details: `docs/shim-probe.md` (what is visible through the engine API) and
`docs/shim-dump.md` (the path to state, snapshots, pitfalls).

#### Single-player saves (`.state`) — no shim, no Steam

A single-player game can be imported straight from its save file: exact score,
cities, army, stars, kills and losses at save time (format v124 reverse-
engineered from scratch — community tools no longer read it):

```bash
polytopia-analyzer import-state          # newest save from the game folder
polytopia-analyzer import-state <file.state> -o data/sp.json
polytopia-analyzer import-state --watch  # the match is written as you play
```

The game rewrites the save on every action, so `--watch` builds a turn-by-turn
history of a live game (a snapshot per turn + city captures from the action
history) — no shim, no Steam launch options, on any platform. The web UI has an
"Import from save (.state)" card for one-off imports.

Single-player only: in live multiplayer this would be cheating, and MP saves in
the current game version are stubs. Format and limitations:
`docs/state-format.md`.

The architecture allows plugging in other sources too (state export via PolyMod
on Steam/Proton, OCR screenshots) — the match format is source-independent.

### Install for players (no terminal)

The ready build is a single executable; no Python required.

1. Unpack `polytopia-analyzer-<version>-linux-x86_64.tar.gz`.
2. Run `install.sh` from the folder (double click). It also works without
   installing: just run the `polytopia-analyzer` file.
3. **"Polytopia Match Analyzer"** appears in the application menu — launching
   it opens a browser with the local page.

Then: "Read from game (Steam)" card → "Enable", open a replay by link,
"Import snapshots", "Build report". The detailed player guide is
`docs/PLAYER.md` (in Russian; copied into the archive as `README.md`).

### Install from source

```bash
git clone https://github.com/koffeny301/polytopia-analyzer.git
cd polytopia-match-analyzer
python3 -m venv .venv
.venv/bin/pip install -e .          # console command polytopia-analyzer
.venv/bin/pip install pytest        # for development
```

The command is installed **inside `.venv/bin`, not into PATH** — without
activating the environment the shell won't find it (`Unknown command` in fish).
Activate the environment or call it by full path:

```bash
source .venv/bin/activate           # bash/zsh
source .venv/bin/activate.fish      # fish
# or without activating:
.venv/bin/polytopia-analyzer -h
```

Run without installing: `.venv/bin/python -m polytopia_analyzer ...`

### Commands

```bash
# 1. Create a match table template (3 players, 30 turns)
polytopia-analyzer template --players "koffeny301,Wolf_Star,NovaAi" \
    --turns 30 --mode Domination --title "Match with friends" \
    --link "https://share.polytopia.io/g/<uuid>" -o data/match.csv

# 2. Open a replay by link (to fill in the table)
polytopia-analyzer open "https://share.polytopia.io/g/<uuid>" [--launch]

# 3. Build the report (+ console summary)
polytopia-analyzer analyze data/match.csv                 # -> data/match_report.html
polytopia-analyzer analyze data/match.csv -o report.html --open

# 4. Validate match data (errors/warnings/completeness)
polytopia-analyzer validate data/match.csv

# Convert CSV <-> JSON
polytopia-analyzer convert data/match.csv data/match.json

# 5. Enable/disable reading from the game (Steam launch options)
polytopia-analyzer install-shim status
polytopia-analyzer install-shim enable --restart-steam
polytopia-analyzer install-shim disable --restart-steam

# 6. Import game-state snapshots (shim) — one-off or watching the folder
polytopia-analyzer import-inbox -o data/shim.json --title "Timeless Battles"
polytopia-analyzer import-inbox --watch        # updates the match as the replay plays

# 7. Import a single-player save (.state) — path or auto-find the newest
polytopia-analyzer import-state                # finds the save itself
polytopia-analyzer import-state ~/game.state -o data/sp.json --title "My game"
polytopia-analyzer import-state --watch        # writes the match as you play
```

#### Example data

- `examples/real_match_shim_v2.json` / `.csv` — a **real match** captured by
  shim v2 from the Timeless Battles replay (KOF vs Goylem, 12 turns): score,
  cities, army, income, kills, losses, captures and damage — all 9 metrics
  automatically (damage is reconstructed from the command log and unit HP).
- `examples/demo_match.csv` / `.json` — a synthetic 30-turn demo match
  (3 players, all metrics filled); generated by `examples/make_demo.py`.

```bash
polytopia-analyzer analyze examples/real_match_shim_v2.csv -o reports/real.html --open
polytopia-analyzer analyze examples/demo_match.csv -o reports/demo.html --open
```

### Web UI (localhost)

The most convenient way to work is the local web page:

```bash
polytopia-analyzer web                        # http://127.0.0.1:8765, opens in the browser
polytopia-analyzer web --port 9000 --no-browser
```

UI features:

- **match editor**: metadata, players (name/tribe/color/"this is you"),
  a "turn × player" table with all metrics; empty cells = data unknown;
- **turn-by-turn input wizard**: "Table / Wizard" toggle — a card for one turn
  across all players, keyboard-only entry (Enter — next field, Ctrl+Enter —
  next turn, Esc — table), fill progress and metric help
  ([docs/metrics.md](docs/metrics.md));
- **OCR of the final scoreboard**: "📷 Upload scoreboard screenshot" — numbers
  are recognized locally (tesseract), then you click a number on the image and
  assign player/metric/turn; dubious recognitions are visible in the confidence
  column. Nothing is guessed automatically;
- **list of saved matches** (`data/` directory): load, delete;
- **file upload** of CSV/JSON straight into the browser;
- **replay link**: paste `share.polytopia.io/g/...` — get Steam/mobile links
  with "copy" buttons;
- **"Data validation" panel**: errors/warnings/completeness in real time;
  clicking an issue jumps to the cell, suspicious cells are highlighted;
  saving with errors is blocked;
- **"📊 Build report"** — the HTML report itself, opens in a new tab;
- **export** to CSV/JSON, save with Ctrl+S.

The server listens on 127.0.0.1 only (data never leaves the machine) and is
implemented on the standard `http.server` with no external dependencies.
Stop with Ctrl+C in the terminal (or `pkill -f "polytopia-analyzer web"`).

Scoreboard OCR needs extra dependencies (optional — everything else works
without them): system [tesseract](https://tesseract-ocr.github.io/tessdoc/Installation.html)
(Linux: `pacman -S tesseract` / `apt install tesseract-ocr`, Windows:
UB-Mannheim installer) and Python packages:

```bash
.venv/bin/pip install -e ".[ocr]"
```

If tesseract is missing, the OCR button shows a clear error with instructions
instead of crashing. OCR is tucked into a collapsible "🧪 Experimental features"
section.

### Desktop app (Tauri)

A native window on top of the same web UI: the Tauri shell starts the
PyInstaller binary in `web` mode as a local server (sidecar) and shows the UI
in a WebKit window — no browser needed. The release format is `*.AppImage`
(also works on Steam Deck in desktop mode).

On first launch the UI offers two modes (toggle — the "⚙ Mode" button in the
header):

- **"Without shim"** — nothing gets installed: single-player save imports
  (`.state`), CSV/JSON upload, manual entry;
- **"With shim"** — an installer card with preflight checks (Steam found,
  profile config, game installed and is a native Linux build, shim library
  compiled), Enable/Disable buttons and snapshot import from replays.

Building from source:

```bash
./scripts/build_release.sh                        # sidecar (PyInstaller + shim)
cp dist/polytopia-analyzer-*/polytopia-analyzer \
   desktop/src-tauri/binaries/polytopia-analyzer-x86_64-unknown-linux-gnu
cd desktop && npm install && npm run tauri build  # AppImage in src-tauri/target/release/bundle/
```

Releases are automatic: push a `v*` tag → GitHub Actions builds the AppImage
and tar.gz and uploads them to Releases.

### Data format

#### CSV

Metadata and players live in comments; the table follows the course of the
match. Strict format specification: [docs/csv-spec.md](docs/csv-spec.md).

```csv
# title: Match with friends
# date: 2026-11-12
# mode: Domination
# map: Large · Continent
# link: https://share.polytopia.io/g/a4d531fd-1b81-428c-d81f-08dae2baaab2
# turn_limit: 30
# player: koffeny301; Vengir; #e6194B; human
# player: Wolf_Star; Bardur; #4363d8
turn,player,score,income,army,cities,kills,losses,captured,lost_cities,damage
1,koffeny301,420,2,1,1,0,0,0,0,0
1,Wolf_Star,450,3,2,1,0,0,0,0,0
...
```

Player line format: `# player: name; tribe; color; human` (tribe/color/human
are optional).

#### Columns

| Column        | Meaning                                      | Kind         |
|---------------|----------------------------------------------|--------------|
| `turn`        | turn number                                  | integer      |
| `player`      | player name                                  | string       |
| `score`       | total worth (score) at end of turn           | snapshot     |
| `income`      | income, ☆ per turn                           | snapshot     |
| `army`        | army size at end of turn                     | snapshot     |
| `cities`      | total city count at end of turn              | snapshot     |
| `kills`       | enemy units killed this turn                 | increment    |
| `losses`      | own units lost this turn                     | increment    |
| `captured`    | cities captured this turn                    | increment    |
| `lost_cities` | cities lost this turn                        | increment    |
| `damage`      | damage dealt this turn                       | increment    |

All metrics except `turn`/`player` are optional — missing columns and empty
cells count as zeros. Turns can be filled selectively: charts are drawn from
recorded points, standings from the last known score.

#### JSON

```json
{
  "meta": {"title": "...", "mode": "...", "link": "https://share.polytopia.io/g/..."},
  "players": [{"name": "...", "tribe": "...", "color": "#rrggbb", "is_human": true}],
  "turns": [{"turn": 1, "player": "...", "score": 420, "kills": 0, ...}]
}
```

### What the report shows

- Winner banner and final standings (score, growth, income, K/D, cities, damage);
- Total-worth dynamics per turn (like the Civilization score graph);
- Lead timeline (who was ahead on each turn);
- Economy: ☆/turn income and city count;
- Army: size per turn and player rank changes;
- Combat: cumulative kills/losses, per-turn damage bars (when damage data
  exists) and combat-outcome comparison incl. total damage dealt;
- Game review (chess.com-style, heuristic): per-player accuracy, per-turn
  verdicts (great/mistake/blunder), advantage curve with key-moment markers;
- Awards: "Warrior", "Economist", "Conqueror", "Sniper", "Strategist".

The report is self-contained: plotly.js is embedded in the HTML (~1 MB), no
internet needed. The `--cdn` flag shrinks the file but requires network when
viewing.

### Building a release

```bash
scripts/build_release.sh          # shim + onefile + installer + archive
```

Result in `dist/`:

```
polytopia-analyzer-0.7.3-linux-x86_64/
    polytopia-analyzer          single file ~45 MB (Python inside)
    install.sh                  installs into ~/.local + menu shortcut
    polytopia-analyzer.desktop.in, icon.svg
    README.md                   player guide (docs/PLAYER.md)
polytopia-analyzer-0.7.3-linux-x86_64.tar.gz (+ .sha256)
```

The build places `libpolyread_dump.so` inside the package
(`polytopia_analyzer/assets/bin/`), so `install-shim` works without a compiler
and `make`. Only PyInstaller is needed:
`.venv/bin/pip install -e ".[build]"`.

### Development

```bash
.venv/bin/pytest -q        # 213 tests
```

Principles, internals, data format and extension points are described in
[ARCHITECTURE.md](ARCHITECTURE.md) (in Russian).

Structure:

```
polytopia_analyzer/
├── models.py     # Match / Player / TurnRecord + validation
├── storage.py    # CSV/JSON: parsing, serialization, type coercion
├── links.py      # share.polytopia.io links -> uuid -> deep links
├── analysis.py   # derived stats, standings, leaders, awards
├── validation.py # data validation rules (errors/warnings/info)
├── report.py     # HTML report with plotly.js
├── webapp.py     # local web server (HTTP API)
├── importers/    # ocr.py — scoreboard numbers; shim.py — game-state snapshots;
│                 # state.py — single-player saves (.state, LZ4)
├── paths.py      # where data lives: repo or ~/.local/share (onefile)
├── steam.py      # Steam launch options: enable/disable shim, rollback
├── cli.py        # analyze / template / open / convert / install-shim / import-inbox / import-state / web
└── assets/       # plotly-basic.min.js, webui.html
```

### Security & privacy

- No telemetry: the tool sends no data to third-party servers.
- The web server listens on 127.0.0.1 only, rejects foreign `Host` headers
  (DNS rebinding) and cross-origin POST/DELETE (CSRF).
- The shim only reads game state and writes JSON into the user's directory;
  it is enabled/disabled by an explicit action, game files are untouched.
- Details and how to report a vulnerability — [SECURITY.md](SECURITY.md).

#### Legal notice

This project is an independent fan tool: it is not affiliated with, endorsed
by, or sponsored by Midjiwan AB. "The Battle of Polytopia" is a trademark of
Midjiwan AB.

Midjiwan's Terms of Service (section 2.2(a)) prohibit reverse engineering the
game and using Polytopia to develop products or services without Midjiwan's
prior written approval. Reading game state (the shim) and parsing `.state`
saves may fall under that restriction. By using this tool you accept that risk
yourself; the tool is intended solely for analyzing your own finished matches
(replays and single-player saves), is strictly read-only, and never contacts
Midjiwan's servers. If in doubt, request written approval from Midjiwan before
use.

### Third-party components

plotly.js (MIT), Tauri (MIT/Apache-2.0), PyInstaller (GPL with runtime
exception) — see [NOTICE](NOTICE). "The Battle of Polytopia" is a trademark of
Midjiwan AB; this project is not affiliated with Midjiwan AB.

### Roadmap

- [x] Reading game state via LD_PRELOAD shim on Linux (v0.2, M6.1–M6.3)
- [x] Steam launch-option installer instead of the temporary wrapper (v0.2, M6.4)
- [x] Build & distribution: onefile, installer, shortcut, guide (v0.2, M8)
- [x] `.state` single-player save parser: read & import (v0.3, M7.1);
      watch mode during a game + city captures from action history
      + web import (v0.4, M7.2)
- [x] OCR of the scoreboard from the game/screenshots (v0.2, M4)
- [x] Desktop app (Tauri/AppImage) with onboarding and shim installer (v0.5, M11)
- [x] Metrics from game state: army, income, captures + command log (v0.6, M12)
- [x] Dynamic turn count; extra turn 0 removed (v0.7, M17/M13)
- [ ] PolyMod export adapter (state JSON from the Steam client)
- [ ] Comparing several matches of one player (trends)

---

<a id="русский"></a>
## Русский

Анализ матчей **The Battle of Polytopia** с итоговым отчётом в стиле Civilization:
интерактивные графики динамики, итоговая таблица, боевая статистика, смена
лидеров и награды — всё в одном самодостаточном HTML-файле.

```
🏆 Победа: koffeny301 (Vengir) — 3 480 очков (отрыв +412)

 #  Игрок            Счёт  Прирост  ☆/ход  Убито  Потер.   K/D  Гор.захв
 1  koffeny301       3480    +3060    8.4      21       9  2.33         4
 2  Wolf_Star        3068    +2648    7.6      14      15  0.93         2
 3  NovaAi           2740    +2320    7.1       8      17  0.47         1
```

### Откуда берутся данные

**Важно про ссылки на матчи.** Ссылка вида `https://share.polytopia.io/g/<uuid>` —
это только deep-link в приложение: данные матча хранятся на серверах Midjiwan
и загружаются исключительно игровым клиентом. Публичного API для их получения
не существует. Поэтому рабочий процесс такой:

1. `polytopia-analyzer open <ссылка> --launch` — программа извлекает id матча
   и открывает реплей в игре (Steam/мобильная версия).
2. Во время просмотра вы заполняете таблицу матча (CSV) — программа-шаблон
   создаётся командой `template`.
3. `polytopia-analyzer analyze match.csv` — строится HTML-отчёт.

Ссылка на реплей сохраняется в метаданных матча и попадает в отчёт.

**Полуавтомат: снятие счёта прямо с реплея.** `scripts/harvest_replay.py`
снимает кадры автовоспроизведения реплея, распознаёт HUD (`Score`, `Stars (+доход)`,
`Turn`) и складывает два ряда — свой и соперника. Даёт счёт и доход по ходам;
армия, города, убийства и урон на HUD не выводятся (для них нужен PolyMod).
Требуется Linux + KDE (`spectacle`), `tesseract`, X11.

```bash
polytopia-analyzer open <ссылка> --launch      # реплей откроется в игре
.venv/bin/python scripts/harvest_replay.py --seconds 180 --out /tmp/frames
# -> /tmp/frames/series.json
```

**Автомат: чтение состояния игры (Linux, нативная сборка).** Шим `LD_PRELOAD`
(`shim/`) грузится в процесс игры и читает живой `GameState` — счёт, звёзды,
города, убийства и потери, армию, доход городов и захваты городов по каждому
игроку на каждом ходу (формат снимков v2; журнал команд матча — в
`commands.json`). Игра при этом ничего не пишет: только чтение метаданных
и полей.

**Только реплей.** Перед каждой записью шим проверяет, что открыт именно реплей
(класс клиента `ReplayClient`). В живом матче снимков не будет — попытка
отказана и записана в журнал. Это часть обещания: инструмент для разбора
завершённых матчей, а не помощник в игре.

```bash
make -C shim
polytopia-analyzer install-shim enable --restart-steam   # строка запуска в Steam
polytopia-analyzer open <ссылка> --launch                # реплей откроется в Steam
polytopia-analyzer import-inbox --watch                  # матч пишется по ходу реплея
polytopia-analyzer install-shim disable --restart-steam   # откат
```

`install-shim` пишет `LD_PRELOAD=… %command%` в параметры запуска игры в Steam
(с резервной копией конфига и откатом). Steam держит конфиг в памяти, поэтому
установщик останавливает и запускает его заново; без `--restart-steam` он
откажется работать при запущенном Steam. Ручной вариант и путь через временную
обёртку (`shim/install-wrapper.sh`) описаны в `docs/shim-dump.md`.

В веб-интерфейсе то же самое делает карточка «Импорт из игры (шим)».
Подробности: `docs/shim-probe.md` (что видно через API движка) и
`docs/shim-dump.md` (путь к состоянию, снимки, грабли).

#### Сейвы одиночной игры (`.state`) — без шима и Steam

Одиночную партию можно импортировать прямо из сейва игры: точные счёт, города,
армия, звёзды, убийства и потери на момент сохранения (формат v124 разобран
заново — community-инструменты его уже не читают):

```bash
polytopia-analyzer import-state          # свежайший сейв из папки игры
polytopia-analyzer import-state <файл.state> -o data/sp.json
polytopia-analyzer import-state --watch  # партия пишется по ходу игры
```

Игра перезаписывает сейв на каждое действие, поэтому `--watch` строит
помходовую историю живой партии (снимок на ход + захваты городов из истории
действий) — без шима, строки запуска Steam и на любой платформе. В веб-
интерфейсе есть карточка «Импорт из сейва (.state)» для разового импорта.

Только одиночные партии: в живом мультиплеере это было бы читерством, а
сейвы MP в текущей версии игры — заглушки. Формат и ограничения:
`docs/state-format.md`.

Архитектура допускает подключение и других источников (экспорт состояния через
PolyMod на Steam/Proton, OCR-скриншоты) — формат матча
от источника не зависит.

### Установка для игрока (без терминала)

Готовая сборка — один исполняемый файл, Python ставить не нужно.

1. Распакуйте `polytopia-analyzer-<версия>-linux-x86_64.tar.gz`.
2. Запустите `install.sh` из папки (двойной клик). Без установки тоже работает:
   просто запустите файл `polytopia-analyzer`.
3. В меню приложений появится **«Анализатор матчей Polytopia»** — при запуске
   открывается браузер с локальной страницей.

Дальше: карточка «Чтение из игры (Steam)» → «Включить», открыть реплей по ссылке,
«Импортировать снимки», «Построить отчёт». Подробная инструкция для игрока —
`docs/PLAYER.md` (копируется в архив как `README.md`).

### Установка из исходников

```bash
git clone https://github.com/koffeny301/polytopia-analyzer.git
cd polytopia-match-analyzer
python3 -m venv .venv
.venv/bin/pip install -e .          # консольная команда polytopia-analyzer
.venv/bin/pip install pytest        # для разработки
```

Команда ставится **внутрь `.venv/bin`, а не в PATH** — без активации
окружения оболочка её не найдёт (`Unknown command` в fish). Активируйте
окружение или вызывайте по полному пути:

```bash
source .venv/bin/activate           # bash/zsh
source .venv/bin/activate.fish      # fish
# либо без активации:
.venv/bin/polytopia-analyzer -h
```

Запуск без установки: `.venv/bin/python -m polytopia_analyzer ...`

### Команды

```bash
# 1. Создать шаблон таблицы матча (3 игрока, 30 ходов)
polytopia-analyzer template --players "koffeny301,Wolf_Star,NovaAi" \
    --turns 30 --mode Domination --title "Матч с друзьями" \
    --link "https://share.polytopia.io/g/<uuid>" -o data/match.csv

# 2. Открыть реплей по ссылке (для заполнения таблицы)
polytopia-analyzer open "https://share.polytopia.io/g/<uuid>" [--launch]

# 3. Построить отчёт (+ сводка в консоль)
polytopia-analyzer analyze data/match.csv                 # -> data/match_report.html
polytopia-analyzer analyze data/match.csv -o report.html --open

# 4. Проверить данные матча (ошибки/предупреждения/полнота)
polytopia-analyzer validate data/match.csv

# Конвертация CSV <-> JSON
polytopia-analyzer convert data/match.csv data/match.json

# 5. Включить/выключить чтение из игры (строка запуска Steam)
polytopia-analyzer install-shim status
polytopia-analyzer install-shim enable --restart-steam
polytopia-analyzer install-shim disable --restart-steam

# 6. Импорт снимков состояния игры (шим) — разово или следя за папкой
polytopia-analyzer import-inbox -o data/shim.json --title "Timeless Battles"
polytopia-analyzer import-inbox --watch        # обновляет матч по ходу реплея

# 7. Импорт сейва одиночной игры (.state) — путь или автопоиск свежайшего
polytopia-analyzer import-state                # найдёт сейв сам
polytopia-analyzer import-state ~/game.state -o data/sp.json --title "Моя партия"
polytopia-analyzer import-state --watch        # пишет матч по ходу партии
```

#### Примеры данных

- `examples/real_match_shim_v2.json` / `.csv` — **настоящий матч**, снятый шимом v2
  из реплея Timeless Battles (KOF vs Goylem, 12 ходов): счёт, города, армия, доход,
  убийства, потери, захваты и урон — все 9 метрик автоматически (урон
  реконструируется по журналу команд и HP юнитов).
- `examples/demo_match.csv` / `.json` — синтетический демо-матч на 30 ходов
  (3 игрока, все метрики заполнены); генерируется `examples/make_demo.py`.

```bash
polytopia-analyzer analyze examples/real_match_shim_v2.csv -o reports/real.html --open
polytopia-analyzer analyze examples/demo_match.csv -o reports/demo.html --open
```

### Веб-интерфейс (localhost)

Самый удобный способ работы — локальная веб-страница:

```bash
polytopia-analyzer web                        # http://127.0.0.1:8765, откроется в браузере
polytopia-analyzer web --port 9000 --no-browser
```

Возможности интерфейса:

- **редактор матча**: мета-данные, игроки (имя/племя/цвет/«это вы»), таблица
  «ход × игрок» со всеми метриками; пустые ячейки = данные неизвестны;
- **мастер ввода по ходам**: переключатель «Таблица / Мастер» — карточка
  одного хода для всех игроков, ввод без мыши (Enter — дальше, Ctrl+Enter —
  следующий ход, Esc — таблица), прогресс заполнения и справка по метрикам
  ([docs/metrics.md](docs/metrics.md));
- **OCR финального табло**: кнопка «📷 Загрузить скриншот табло» — числа
  распознаются локально (tesseract), затем вы кликаете число на картинке
  и указываете игрока/метрику/ход; сомнительные распознавания видны по
  колонке уверенности. Ничего не угадывается автоматически;
- **список сохранённых матчей** (каталог `data/`): загрузить, удалить;
- **загрузка файла** CSV/JSON прямо в браузер;
- **ссылка на реплей**: вставили `share.polytopia.io/g/...` — получили
  ссылки для Steam/мобильной версии с кнопками «копировать»;
- **панель «Проверка данных»**: ошибки/предупреждения/полнота в реальном
  времени; клик по замечанию ведёт к ячейке, подозрительные ячейки
  подсвечиваются; сохранение с ошибками блокируется;
- **«📊 Построить отчёт»** — тот самый HTML-отчёт, открывается в новой вкладке;
- **экспорт** в CSV/JSON, сохранение по Ctrl+S.

Сервер слушает только 127.0.0.1 (данные не покидают машину), реализован на
стандартном `http.server` без внешних зависимостей. Остановка — Ctrl+C
в терминале (или `pkill -f "polytopia-analyzer web"`).

Для OCR финального табло нужны дополнительные зависимости (опциональны — всё
остальное работает без них): системный [tesseract](https://tesseract-ocr.github.io/tessdoc/Installation.html)
(Linux: `pacman -S tesseract` / `apt install tesseract-ocr`, Windows:
установщик UB-Mannheim) и Python-пакеты:

```bash
.venv/bin/pip install -e ".[ocr]"
```

Если tesseract не найден, кнопка OCR покажет понятную ошибку с инструкцией,
а не упадёт. OCR спрятан в сворачиваемую секцию «🧪 Экспериментальные функции».

### Десктоп-приложение (Tauri)

Нативное окно поверх того же веб-интерфейса: оболочка Tauri поднимает
PyInstaller-бинарник в режиме `web` как локальный сервер (sidecar) и показывает
интерфейс в окне WebKit — браузер не нужен. Релизный формат — `*.AppImage`
(работает и на Steam Deck в desktop-режиме).

При первом запуске интерфейс предлагает два режима (переключение — кнопка
«⚙ Режим» в шапке):

- **«Без шима»** — ничего не устанавливается: импорт сейвов одиночной игры
  (`.state`), загрузка CSV/JSON, ручной ввод;
- **«С шимом»** — карточка установщика с предпроверками (Steam найден, конфиг
  профиля, игра установлена и является нативной Linux-сборкой, библиотека шима
  собрана), кнопки «Включить/Выключить» и импорт снимков из реплеев.

Сборка из исходников:

```bash
./scripts/build_release.sh                        # sidecar (PyInstaller + шим)
cp dist/polytopia-analyzer-*/polytopia-analyzer \
   desktop/src-tauri/binaries/polytopia-analyzer-x86_64-unknown-linux-gnu
cd desktop && npm install && npm run tauri build  # AppImage в src-tauri/target/release/bundle/
```

Публикация релизов автоматическая: push тега `v*` → GitHub Actions собирает
AppImage и tar.gz и выкладывает их в Releases.

### Формат данных

#### CSV

Метаданные и игроки — в комментариях, таблица — по ходу матча.
Строгая спецификация формата: [docs/csv-spec.md](docs/csv-spec.md).

```csv
# title: Матч с друзьями
# date: 2026-11-12
# mode: Domination
# map: Large · Continent
# link: https://share.polytopia.io/g/a4d531fd-1b81-428c-d81f-08dae2baaab2
# turn_limit: 30
# player: koffeny301; Vengir; #e6194B; human
# player: Wolf_Star; Bardur; #4363d8
turn,player,score,income,army,cities,kills,losses,captured,lost_cities,damage
1,koffeny301,420,2,1,1,0,0,0,0,0
1,Wolf_Star,450,3,2,1,0,0,0,0,0
...
```

Формат строки игрока: `# player: имя; племя; цвет; human` (племя/цвет/human необязательны).

#### Колонки

| Колонка       | Смысл                                        | Тип          |
|---------------|----------------------------------------------|--------------|
| `turn`        | номер хода                                   | целое        |
| `player`      | имя игрока                                   | строка       |
| `score`       | общая стоимость (счёт) на конец хода         | снимок       |
| `income`      | доход, ☆ за ход                              | снимок       |
| `army`        | размер армии на конец хода                   | снимок       |
| `cities`      | общее число городов на конец хода            | снимок       |
| `kills`       | убито юнитов врага за ход                    | прирост      |
| `losses`      | потеряно своих юнитов за ход                 | прирост      |
| `captured`    | захвачено городов за ход                     | прирост      |
| `lost_cities` | потеряно городов за ход                      | прирост      |
| `damage`      | нанесённый урон за ход                       | прирост      |

Все метрики кроме `turn`/`player` необязательны — недостающие колонки и пустые
ячейки считаются нулями. Ходы можно заполнять выборочно: графики строятся по
записанным точкам, места считаются по последнему известному счёту.

#### JSON

```json
{
  "meta": {"title": "...", "mode": "...", "link": "https://share.polytopia.io/g/..."},
  "players": [{"name": "...", "tribe": "...", "color": "#rrggbb", "is_human": true}],
  "turns": [{"turn": 1, "player": "...", "score": 420, "kills": 0, ...}]
}
```

### Что показывает отчёт

- Баннер победителя и итоговая таблица (счёт, прирост, доход, K/D, города, урон);
- Динамика общей стоимости по ходам (как график счёта в Civilization);
- Хронология лидерства (кто вёл на каждом ходу);
- Экономика: доход ☆/ход и число городов;
- Армия: размер по ходам и смена мест игроков;
- Боевые действия: накопительные kills/losses, урон по ходам (когда данные
  урона есть) и сравнение боевых итогов, включая суммарный урон;
- Разбор матча в стиле chess.com (эвристика): точность игроков, вердикты по
  ходам (отличный/ошибка/зевк), кривая преимущества с ключевыми моментами;
- Награды: «Воин», «Экономист», «Завоеватель», «Снайпер», «Стратег».

Отчёт самодостаточен: plotly.js встроен в HTML (~1 МБ), интернет не нужен.
Флаг `--cdn` уменьшает файл, но требует сеть при просмотре.

### Сборка релиза

```bash
scripts/build_release.sh          # шим + onefile + установщик + архив
```

Результат в `dist/`:

```
polytopia-analyzer-0.7.3-linux-x86_64/
    polytopia-analyzer          один файл ~45 МБ (Python внутри)
    install.sh                  ставит в ~/.local + ярлык в меню
    polytopia-analyzer.desktop.in, icon.svg
    README.md                   инструкция для игрока (docs/PLAYER.md)
polytopia-analyzer-0.7.3-linux-x86_64.tar.gz (+ .sha256)
```

Сборка кладёт `libpolyread_dump.so` внутрь пакета (`polytopia_analyzer/assets/bin/`),
поэтому `install-shim` работает без компилятора и `make`. Нужен только PyInstaller:
`.venv/bin/pip install -e ".[build]"`.

### Разработка

```bash
.venv/bin/pytest -q        # 213 тестов
```

Принцип работы, внутреннее устройство, формат данных и точки расширения
описаны в [ARCHITECTURE.md](ARCHITECTURE.md).

Структура:

```
polytopia_analyzer/
├── models.py     # Match / Player / TurnRecord + валидация
├── storage.py    # CSV/JSON: парсинг, сериализация, приведение типов
├── links.py      # ссылки share.polytopia.io -> uuid -> deep-links
├── analysis.py   # производная статистика, места, лидеры, награды
├── validation.py # правила проверки данных (ошибки/предупреждения/инфо)
├── report.py     # HTML-отчёт с plotly.js
├── webapp.py     # локальный веб-сервер (HTTP API)
├── importers/    # ocr.py — числа с табло; shim.py — снимки состояния игры;
│                 # state.py — сейвы одиночной игры (.state, LZ4)
├── paths.py      # где данные: репозиторий или ~/.local/share (onefile)
├── steam.py      # параметры запуска Steam: включить/выключить шим, откат
├── cli.py        # analyze / template / open / convert / install-shim / import-inbox / import-state / web
└── assets/       # plotly-basic.min.js, webui.html
```

### Безопасность и приватность

- Никакой телеметрии: программа не отправляет данные на сторонние серверы.
- Веб-сервер слушает только 127.0.0.1, отклоняет чужие `Host` (DNS-rebinding)
  и междоменные POST/DELETE (CSRF).
- Шим только читает состояние игры и пишет JSON в каталог пользователя;
  включается/выключается явным действием, файлы игры не меняются.
- Подробности и как сообщить об уязвимости — [SECURITY.md](SECURITY.md).

#### Правовое предупреждение

Проект — независимый фанатский инструмент: не аффилирован с Midjiwan AB, не
одобрен и не спонсируется ею. «The Battle of Polytopia» — торговая марка
Midjiwan AB.

Условия использования Polytopia (п. 2.2(a)) запрещают реверс-инжиниринг игры и
использование Polytopia для разработки продуктов или услуг без
предварительного письменного разрешения Midjiwan. Чтение состояния игры (шим)
и разбор сейвов `.state` могут подпадать под это ограничение. Используя
инструмент, вы принимаете этот риск на себя; инструмент предназначен только для
разбора ваших собственных завершённых матчей (реплеи и одиночные сейвы),
работает строго на чтение и не обращается к серверам Midjiwan. При сомнениях
запросите письменное разрешение Midjiwan до использования.

### Сторонние компоненты

plotly.js (MIT), Tauri (MIT/Apache-2.0), PyInstaller (GPL с runtime-исключением) —
см. [NOTICE](NOTICE). «The Battle of Polytopia» — торговая марка Midjiwan AB;
проект не аффилирован с Midjiwan AB.

### Дорожная карта

- [x] Чтение состояния игры шимом LD_PRELOAD на Linux (v0.2, M6.1–M6.3)
- [x] Установщик строки запуска Steam вместо временной обёртки (v0.2, M6.4)
- [x] Сборка и распространение: onefile, установщик, ярлык, инструкция (v0.2, M8)
- [x] Парсер `.state`-сейвов одиночной игры: чтение и импорт (v0.3, M7.1);
      watch-режим по ходу партии + захваты городов из истории действий
      + веб-импорт (v0.4, M7.2)
- [x] OCR-распознавание экрана счёта из игры/скриншотов (v0.2, M4)
- [x] Десктоп-приложение (Tauri/AppImage) с онбордингом и установщиком шима (v0.5, M11)
- [x] Метрики из состояния игры: армия, доход, захваты + журнал команд (v0.6, M12)
- [x] Динамическое число ходов; убран лишний ход 0 (v0.7, M17/M13)
- [ ] Адаптер экспорта PolyMod (JSON состояния из Steam-клиента)
- [ ] Сравнение нескольких матчей одного игрока (тренды)
