# Security

**[English](#english) | [Русский](#русский)**

<a id="english"></a>
## English

### Threat model and guarantees

Polytopia Match Analyzer is a local tool. Guarantees the program provides:

- **No telemetry, no external requests.** The tool sends no data to third-party
  servers. The only network accesses are: loading plotly.js from a CDN when a
  report is built with `--cdn` (by default plotly is embedded in the report and
  no requests are made), and the deep link into Steam to open a replay.
- **Web server is localhost-only.** The server binds to 127.0.0.1 and accepts
  requests only with loopback `Host` headers; cross-origin POST/DELETE requests
  are rejected (protection against CSRF and DNS rebinding). An explicit
  `--host 0.0.0.0` is a deliberate user choice; the Host filter is disabled in
  that mode.
- **The shim is read-only.** The LD_PRELOAD library does not modify game
  memory, does not tamper with traffic and does not affect gameplay; it only
  copies state values into JSON files inside the user's directory. Snapshots
  are taken only while a replay is playing.
- **Files are written only inside the program's directories**
  (`~/.local/share/polytopia-analyzer` and the user-chosen matches directory);
  names from URLs are checked against directory escape (path traversal is
  excluded).
- **Match data is escaped** when inserted into HTML reports and the web UI
  (protection against XSS via player names/titles coming from game data).

### Shim installation

The shim is enabled only by an explicit user action (a button in the UI or
`install-shim enable`) and changes only the game's launch options in the Steam
configuration (adding `LD_PRELOAD`), with rollback support
(`install-shim disable`). Game files are not modified.

### Reporting a vulnerability

Please do not create a public issue. Report privately: use the
[Private vulnerability reporting](https://docs.github.com/en/code-security/security-advisories/guidance-on-reporting-and-writing/privately-reporting-a-security-vulnerability)
button (Security → Report a vulnerability) or contact the repository owner
directly. We will respond within 7 days.

---

<a id="русский"></a>
## Русский

### Модель угроз и гарантии

Polytopia Match Analyzer — локальный инструмент. Гарантии, которые даёт программа:

- **Нет телеметрии и внешних запросов.** Программа не отправляет никакие данные
  на сторонние серверы. Единственные сетевые обращения — загрузка plotly.js
  из CDN, если отчёт открыт в варианте `--cdn` (по умолчанию plotly встроен
  в отчёт, запросов нет), и deep-link в Steam для открытия реплея.
- **Веб-сервер только для localhost.** Сервер привязан к 127.0.0.1 и принимает
  запросы только с loopback-заголовками `Host`; междоменные POST/DELETE
  отклоняются (защита от CSRF и DNS-rebinding). Явный `--host 0.0.0.0` —
  осознанный выбор пользователя, в этом режиме Host-фильтр отключается.
- **Шим только читает.** LD_PRELOAD-библиотека не модифицирует память игры,
  не подменяет трафик и не влияет на игровой процесс; она лишь копирует
  значения состояния в JSON-файлы в каталог пользователя. Снимки делаются
  только при проигрывании реплеев.
- **Файлы пишутся только в каталоги программы** (`~/.local/share/polytopia-analyzer`
  и выбранный пользователем каталог матчей); имена из URL проходят проверку
  на выход за пределы каталога (path traversal исключён).
- **Данные матчей экранируются** при вставке в HTML-отчёты и веб-интерфейс
  (защита от XSS через имена игроков/названия из игровых данных).

### Установка шима

Шим включается только явным действием пользователя (кнопка в UI или
`install-shim enable`) и меняет лишь параметры запуска игры в конфигурации
Steam (добавляется `LD_PRELOAD`), с возможностью отката (`install-shim disable`).
Файлы игры не изменяются.

### Сообщить об уязвимости

Пожалуйста, не создавайте публичный issue. Напишите приватно: используйте
кнопку [Private vulnerability reporting](https://docs.github.com/en/code-security/security-advisories/guidance-on-reporting-and-writing/privately-reporting-a-security-vulnerability)
(Security → Report a vulnerability) или свяжитесь с владельцем репозитория
напрямую. Ответим в течение 7 дней.
