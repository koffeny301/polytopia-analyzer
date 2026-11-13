# Polytopia Match Analyzer

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

## Откуда берутся данные

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
города, убийства и потери по каждому игроку на каждом ходу. Игра при этом
ничего не пишет: только чтение метаданных и полей.

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

### Сейвы одиночной игры (`.state`) — без шима и Steam

Одиночную партию можно импортировать прямо из сейва игры: точные счёт, города,
армия, звёзды, убийства и потери на момент сохранения (формат v124 разобран
заново — community-инструменты его уже не читают):

```bash
polytopia-analyzer import-state          # свежайший сейв из папки игры
polytopia-analyzer import-state <файл.state> -o data/sp.json
```

Только одиночные партии: в живом мультиплеере это было бы читерством, а
сейвы MP в текущей версии игры — заглушки. Формат и ограничения:
`docs/state-format.md`.

Архитектура допускает подключение и других источников (экспорт состояния через
PolyMod на Steam/Proton, OCR-скриншоты) — формат матча
от источника не зависит.

## Установка для игрока (без терминала)

Готовая сборка — один исполняемый файл, Python ставить не нужно.

1. Распакуйте `polytopia-analyzer-<версия>-linux-x86_64.tar.gz`.
2. Запустите `install.sh` из папки (двойной клик). Без установки тоже работает:
   просто запустите файл `polytopia-analyzer`.
3. В меню приложений появится **«Анализатор матчей Polytopia»** — при запуске
   открывается браузер с локальной страницей.

Дальше: карточка «Чтение из игры (Steam)» → «Включить», открыть реплей по ссылке,
«Импортировать снимки», «Построить отчёт». Подробная инструкция для игрока —
`docs/PLAYER.md` (копируется в архив как `README.md`).

## Установка из исходников

```bash
cd /mnt/data/проекты/polytopia-match-analyzer
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

## Команды

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
```

Демо: `examples/demo_match.csv` (сгенерирован `examples/make_demo.py`).

```bash
polytopia-analyzer analyze examples/demo_match.csv -o reports/demo.html --open
```

## Веб-интерфейс (localhost)

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
а не упадёт.

## Формат данных

### CSV

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

### Колонки

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

### JSON

```json
{
  "meta": {"title": "...", "mode": "...", "link": "https://share.polytopia.io/g/..."},
  "players": [{"name": "...", "tribe": "...", "color": "#rrggbb", "is_human": true}],
  "turns": [{"turn": 1, "player": "...", "score": 420, "kills": 0, ...}]
}
```

## Что показывает отчёт

- Баннер победителя и итоговая таблица (счёт, прирост, доход, K/D, города, урон);
- Динамика общей стоимости по ходам (как график счёта в Civilization);
- Хронология лидерства (кто вёл на каждом ходу);
- Экономика: доход ☆/ход и число городов;
- Армия: размер по ходам и смена мест игроков;
- Боевые действия: накопительные kills/losses и сравнение боевых итогов;
- Награды: «Воин», «Экономист», «Завоеватель», «Снайпер», «Стратег».

Отчёт самодостаточен: plotly.js встроен в HTML (~1 МБ), интернет не нужен.
Флаг `--cdn` уменьшает файл, но требует сеть при просмотре.

## Сборка релиза

```bash
scripts/build_release.sh          # шим + onefile + установщик + архив
```

Результат в `dist/`:

```
polytopia-analyzer-0.2.0-linux-x86_64/
    polytopia-analyzer          один файл ~45 МБ (Python внутри)
    install.sh                  ставит в ~/.local + ярлык в меню
    polytopia-analyzer.desktop.in, icon.svg
    README.md                   инструкция для игрока (docs/PLAYER.md)
polytopia-analyzer-0.2.0-linux-x86_64.tar.gz (+ .sha256)
```

Сборка кладёт `libpolyread_dump.so` внутрь пакета (`polytopia_analyzer/assets/bin/`),
поэтому `install-shim` работает без компилятора и `make`. Нужен только PyInstaller:
`.venv/bin/pip install -e ".[build]"`.

## Разработка

```bash
.venv/bin/pytest -q        # 105 тестов
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

## Дорожная карта

- [x] Чтение состояния игры шимом LD_PRELOAD на Linux (v0.2, M6.1–M6.3)
- [x] Установщик строки запуска Steam вместо временной обёртки (v0.2, M6.4)
- [x] Сборка и распространение: onefile, установщик, ярлык, инструкция (v0.2, M8)
- [ ] Адаптер экспорта PolyMod (JSON состояния из Steam-клиента)
- [x] Парсер `.state`-сейвов одиночной игры: чтение и импорт (v0.3, M7.1);
      походовые захваты из истории действий — M7.2
- [x] OCR-распознавание экрана счёта из игры/скриншотов (v0.2, M4)
- [ ] Сравнение нескольких матчей одного игрока (тренды)
