# Emergency API: текущее использование и руководство по замене

## Назначение документа

Этот документ описывает интеграцию Robopark с upstream API
`https://emergency.sdc.yandex-team.ru/api/v1`, всех её прямых и косвенных
потребителей и требования к замене upstream-сервиса.

Документ составлен по текущему коду репозитория. Он не описывает внутреннюю
реализацию самого Emergency API и не подтверждает свойства, которые Robopark
не проверяет. В частности, полный контракт production-ответа нельзя восстановить
только из кода: конфигурация отображаемых JSON-полей и диагностические правила
хранятся в базе и могут отличаться от seed-файлов репозитория.

## Краткий вывод

Прямой HTTP-вызов Emergency сосредоточен в одном месте:
`apps/api/src/robopark_api/services/emergency_client.py`. Сейчас выполняется
одна операция:

```http
GET https://emergency.sdc.yandex-team.ru/api/v1/{vin}/
Cookie: <общая cookie из настроек Robopark>
Accept: application/json
X-Requested-With: XMLHttpRequest
User-Agent: Mozilla/... Robopark/1
```

Однако заменить только строку `EMERGENCY_BASE` безопасно можно лишь тогда,
когда новый сервис полностью повторяет:

- URL и trailing slash;
- cookie-аутентификацию и её признаки отказа;
- JSON-ответ в виде объекта;
- набор и смысл полей внутри этого объекта;
- поведение по VIN `YASADR`;
- допустимую нагрузку от пользовательского polling и фонового keep-alive.

Если хотя бы один из этих пунктов отличается, нужен адаптер, который преобразует
ответ новой API во внутренний канонический payload Robopark. Это предпочтительная
точка миграции: остальные backend- и frontend-компоненты можно оставить без
изменения.

## Карта потока данных

```text
Browser
  ├─ /emergency/*, /robots/:vin/check, карточка робота
  │    └─ Robopark API: resolve / snapshot / section
  ├─ /robots
  │    └─ реестр использует только уже свежий Emergency-кэш
  └─ /admin
       └─ установка и ручная проверка cookie

Robopark API
  ├─ VIN-нормализация и проверка доступа через Tracker
  ├─ emergency_cache (TTL, single-flight, межпроцессный merge)
  │    └─ emergency_client.fetch_robot_payload
  │         └─ GET emergency.sdc.yandex-team.ru/api/v1/{vin}/
  ├─ emergency_snapshot (каноническая телеметрия)
  │    ├─ diagnostic_rules
  │    └─ diagnostic_unknowns
  ├─ emergency_sections (динамические разделы из БД)
  ├─ keep-alive
  ├─ статус интеграции и readiness
  └─ отчёт для администраторов о протухшей cookie
```

## 1. Точный upstream HTTP-контракт

### 1.1 URL и метод

- Base URL жёстко задан константой `EMERGENCY_BASE` и не настраивается через
  environment или базу данных.
- Для робота формируется `GET {EMERGENCY_BASE}/{vin}/`.
- После VIN всегда добавляется `/`.
- Query-параметры и request body отсутствуют.
- Используется синхронный `httpx.Client`.
- Таймаут клиента — 30 секунд.
- Redirect разрешён (`follow_redirects=True`).
- При каждом фактическом upstream-запросе создаётся новый HTTP-клиент; постоянный
  connection pool между вызовами не используется.
- Автоматических повторов при timeout, `5xx` или сетевой ошибке нет.

### 1.2 Заголовки

Клиент отправляет:

| Заголовок | Значение/назначение |
| --- | --- |
| `Accept` | `application/json` |
| `User-Agent` | browser-like Linux/Chrome UA с суффиксом `Robopark/1` |
| `X-Requested-With` | `XMLHttpRequest` |
| `Cookie` | выбранный кандидат общей Emergency-cookie |

Новая API должна либо принимать эти заголовки, либо transport-слой должен быть
изменён. Нельзя переносить cookie в query string или логи.

### 1.3 Cookie-аутентификация

Cookie хранится в таблице platform settings в зашифрованном виде. Ключ шифрования
производится из `SECRET_KEY`. Если ключ отсутствует или был заменён, сохранённое
значение не расшифровывается и интеграция выглядит как ненастроенная.

Пользователь может вставить либо значение cookie, либо строку вида
`Cookie: ...`. Клиент удаляет префикс `Cookie:` и пробует кандидаты в таком
порядке:

1. вся переданная cookie;
2. отдельно пара `Session_id=...`, если она есть;
3. отдельно пара `sessionid2=...`, если она есть.

Следующий кандидат пробуется только после ответа, распознанного как ошибка
авторизации. Первый ответ, не похожий на auth-ошибку, завершает перебор — даже
если это `5xx` или неожиданный HTML.

Ответ считается auth-ошибкой, если выполнено одно из условий:

- HTTP status равен `401` или `403`;
- `Content-Type` содержит `text/html`, а первые 4096 символов body содержат
  один из маркеров: `passport.yandex`, `oauth`, `login`, `войти`, `авторизац`.

Если все кандидаты отклонены, поднимается `EmergencyAuthError`.

### 1.4 Успешный ответ

После проверки авторизации клиент требует:

- ответ не должен быть HTML;
- status должен быть меньше 400;
- body должен декодироваться как JSON;
- корневое значение JSON должно быть объектом (`dict`).

Минимальная технически принимаемая форма — `{}`. Клиент не проверяет наличие
`vin`, соответствие VIN запросу, версию схемы или обязательные поля. Отсутствующие
значения ниже превращаются в `null`, `нет данных` либо пустые списки.

### 1.5 Ошибки upstream и их локальное отображение

В transport-слое существуют только два класса ошибок:

| Условие | Внутренняя ошибка |
| --- | --- |
| `401`, `403` или login HTML для всех cookie-кандидатов | `EmergencyAuthError` |
| timeout, DNS/TLS/connection error | `EmergencyError` |
| любой другой status `>= 400` | `EmergencyError` |
| неожиданный HTML, не похожий на login | `EmergencyError` |
| невалидный JSON или JSON не-объект | `EmergencyError` |

Дальнейшее HTTP-отображение зависит от локального endpoint:

| Локальный endpoint | Auth error | Остальная upstream-ошибка |
| --- | ---: | ---: |
| `/emergency/*` и legacy `/mechanic/emergency/*` | `403 emergency_cookie_invalid` | `502 emergency_upstream_error` |
| `PUT /admin/settings/emergency-cookie` | `401 emergency_cookie_invalid` | `503 emergency_upstream_unavailable` |
| `POST /admin/settings/emergency-cookie/check` | `401 emergency_cookie_invalid` | `503 emergency_upstream_unavailable` |

Например, upstream `404` сейчас становится общей недоступностью, а не локальным
`robot_not_found`. Если новая API различает «робот не найден», rate limit и
временную недоступность, это различие следует явно добавить в адаптер и локальный
error contract.

### 1.6 VIN-контракт

Robopark нормализует пользовательский ввод до `YASADR` + 11 цифр:

- `447` → `YASADR00000000447`;
- готовый VIN приводится к верхнему регистру;
- после `YASADR` сохраняются последние 11 найденных цифр;
- в произвольной строке используется первая последовательность цифр.

Именно нормализованный VIN подставляется в upstream URL. Новый сервис должен
принимать этот идентификатор либо адаптер должен преобразовывать его в новый ID.
Нужна и обратная связь для Tracker/UI: короткий номер вычисляется из цифр VIN.

## 2. Кэширование и профиль нагрузки

### 2.1 Локальный cache и single-flight

`emergency_cache.get_robot_payload` — основная точка чтения для пользовательских
endpoint и keep-alive.

- TTL сырого payload — **2,5 секунды**.
- In-process cache хранит payload по VIN вместе с identity активной cookie.
- Одновременные запросы одного процесса для одинаковых `(cookie identity, VIN)`
  объединяются в один upstream-вызов.
- Запросы разных VIN выполняются независимо.
- При смене cookie кэш очищается, а результаты уже выполняющегося запроса старой
  generation не записываются и не изменяют статус новой cookie.
- Перед долгим HTTP-вызовом request DB session освобождает соединение с БД.

### 2.2 Межпроцессный merge

При включённом `ROBOPARK_LIVE_MERGE` процессы API координируются через файлы в
`data/live-merge` (или `LIVE_MERGE_DIR`): namespace `emergency.robot`, ключ
`{cookie_identity}:{vin}`. Один процесс делает upstream-запрос, остальные читают
его JSON-результат. Время ожидания по умолчанию — 25 секунд.

При замене API нужно:

- сохранить дедупликацию или пересчитать допустимую upstream-нагрузку;
- включить provider/schema version в ключ, если старый и новый provider работают
  одновременно;
- очистить оба уровня кэша при переключении credentials/provider;
- убедиться, что нормализованный результат остаётся JSON-объектом.

### 2.3 Browser polling

Snapshot и выбранный раздел робота обновляются с базовым интервалом 10 секунд.
Polling приостанавливается в скрытой вкладке и offline, использует jitter и
backoff. На одном экране обычно одновременно запрашиваются snapshot и один
section. Backend single-flight объединяет их в один upstream-запрос, если они
попадают в одну cache generation.

### 2.4 Keep-alive

Фоновый цикл запускается вместе с API:

- проходит до 20 недавно успешно запрошенных VIN;
- если ring пуст, может использовать `emergency_keepalive_seed_vin`;
- цикл повторяется через случайные 90–120 секунд;
- между VIN выдерживается 0,9 секунды;
- использует тот же cache и transport;
- прекращает текущий цикл после auth-ошибки;
- при временной ошибке пишет warning и продолжает со следующим VIN;
- не работает во время host maintenance.

Новая API должна разрешать такой технический трафик. Если у неё короткоживущий
token, refresh-token или отдельный health endpoint, keep-alive и credential state
нужно переделать, а не имитировать старую cookie.

## 3. Все прямые потребители upstream-клиента

### 3.1 Пользовательское чтение через cache

`routers/emergency.py` вызывает cache для трёх операций:

| Endpoint | Для чего нужен upstream payload |
| --- | --- |
| `POST /emergency/resolve` | проверить существование/доступность робота перед возвратом VIN и доступных разделов |
| `GET /emergency/{vin}/snapshot` | построить каноническую телеметрию и диагностические события |
| `GET /emergency/{vin}/sections/{section_id}` | отобразить выбранные raw-поля по конфигурации раздела |

Legacy endpoints `/mechanic/emergency/resolve` и
`/mechanic/emergency/{vin}/sections/{section_id}` переиспользуют те же функции.

Перед обращением к Emergency выполняется локальная авторизация и VIN scope:

- admin/royal имеют доступ к любому VIN;
- driver имеет доступ к любому VIN;
- остальные роли должны иметь подходящий Tracker-тикет в доступном парке;
- ошибка Tracker даёт `502 tracker_upstream_error`;
- неподтверждённый scope даёт `403 emergency_vin_out_of_scope`.

Это правило не является частью Emergency API, но обязательно должно сохраниться
после миграции: upstream credential общий и сам по себе открывает данные всех
роботов.

### 3.2 Установка credential администратором

`PUT /admin/settings/emergency-cookie` вызывает transport напрямую, без cache:

1. нормализует проверочный номер робота;
2. проверяет новую cookie реальным запросом;
3. только после успеха атомарно сохраняет cookie, новую identity и статус;
4. очищает payload cache;
5. закрывает открытые отчёты о протухшей cookie;
6. пишет audit event.

При token/OAuth-аутентификации этот endpoint, schema, тексты UI и хранилище
credential нужно переименовать или временно поддержать в compatibility-режиме.

### 3.3 Ручная проверка credential

`POST /admin/settings/emergency-cookie/check` тоже вызывает transport напрямую.
VIN берётся из request или из последнего элемента keep-alive ring. Результат
обновляет публичные поля статуса интеграции и открывает/закрывает административный
отчёт.

### 3.4 Keep-alive

`services/emergency_keepalive.py` получает payload через общий cache, поэтому
участвует в том же single-flight и обновляет те же credential-состояния.

## 4. Косвенные потребители сырого payload

### 4.1 Snapshot телеметрии

`services/emergency_snapshot.py` преобразует свободный upstream JSON в стабильный
ответ `EmergencySnapshotOut`:

| Поле Robopark | Поля/варианты upstream |
| --- | --- |
| `online` | `isOnline` |
| `speed` | `velocity`; scalar или вложенные `value`, `speed`, `mps`, `kmh` |
| `charge_percent` | `batteriesStatus.chargePercents`, `chargePercentage`; fallback на battery1 |
| `battery1_percent` | `batteriesStatus.battery1` и его числовые aliases |
| `battery2_percent` | `batteriesStatus.battery2`; игнорируется при `isConnected=false` |
| `disk_percent` | `disk`, fallback `diskUsage`; поддерживаются вложенные percent/usage aliases |
| `mode` | по порядку `autoMode`, `sadrMode`, `lifecycleStatus`, `profile` |
| `icp_label`, `icp_ok` | `icp` |
| `lte_label`, `lte_ok` | `lte` |
| `connection` | `isWired`, `wired`, `ethernet`, `ethernetConnected`, затем `lte` и `isOnline` |
| `error_banner` | первый непустой элемент из `lastCritNotification`, `lastErrorNotification`, `errors`, `panics`, `notifications` |
| `lat`, `lon` | `position.lat`; `position.lon` или `position.lng` |
| `heading_deg` | `position.yaw`, `heading`, `angle`; fallback `robotHudData.heading` |
| `wheels_fault` | `wheelsBroken`: индексы 0–5, aliases `fl/lf/.../rr` либо описательные строки |
| `diagnostic_events` | diagnostic rules по пяти стандартным и любым настроенным JSON-path |

Парсер намеренно терпим к типам: boolean/string/number/dict во многих полях
нормализуются. При миграции эту терпимость лучше сохранить в provider adapter,
но новый канонический контракт следует покрыть schema/contract-тестами.

### 4.2 Диагностические правила и неизвестные ошибки

По умолчанию источниками ошибок считаются:

- `lastCritNotification`;
- `lastErrorNotification`;
- `errors`;
- `panics`;
- `notifications`.

Администратор может создать diagnostic rule с произвольным допустимым
`source_path`. Следовательно, статический поиск по репозиторию не даёт полного
списка используемых полей production payload. Snapshot сопоставляет exact/regex
правила, возвращает нормализованные события, а неизвестные диагностические units
сохраняются отдельно с ограничением частоты. Полные upstream snapshots в таблицу
unknowns не записываются.

Перед миграцией нужно экспортировать все активные diagnostic rules и проверить
каждый `source_path` по mapping новой API.

#### Как устроена разметка ошибки

Разметка хранится в таблице `diagnostic_rules`. Одно правило содержит:

| Поле | Назначение |
| --- | --- |
| `source_path` | путь к источнику сигнала в upstream JSON |
| `match_kind` | `exact` или `regex` |
| `pattern` | точное значение либо регулярное выражение |
| `example` | тестовый пример для preview редактора |
| `title` | короткое пользовательское название ошибки |
| `description` | инструкция/расшифровка для пользователя |
| `severity` | `critical`, `warning` или `info` |
| `part` | человекочитаемая часть робота |
| `preferred_view` | `top`, `front`, `rear`, `left`, `right` или `isometric` |
| `x`, `y` | координаты маркера на фотографии от 0 до 1 |
| `indicator` | вид маркера: `point`, `outline` или `zone` |
| `is_enabled` | участвует ли правило в классификации |
| `sort_order` | порядок среди событий одинаковой важности |

`source_path` использует сегменты через точку. Сегмент может быть именем поля
либо неотрицательным индексом массива: например, `errors` или
`data.errors.0`. Длина пути ограничена 256 символами; сегменты с `__` и символы
вне разрешённого набора отклоняются. В отличие от renderer разделов, matcher
умеет проходить не только объекты, но и массивы по числовому индексу.

#### Как raw-сигнал превращается в событие

1. Matcher собирает пять стандартных error sources и `source_path` всех правил,
   включая отключённые. Поэтому отключение custom-правила убирает его маркер, но
   не должно скрыть сам raw-сигнал.
2. Значение источника раскладывается на диагностические units. Массивы
   разворачиваются по элементам; структурированные ошибки сохраняют исходные
   `code`, `message`, `text`, `path` и неизвестные вложенные данные.
3. Для `exact` строка и whitespace сравниваются без нормализации регистра.
   Нестроковые значения переводятся в стабильный canonical JSON.
4. Для `regex` выполняется `search`, а не full match. Используется VERSION0;
   pattern ограничен 512 символами, один counted repeat — 1000, оценка expansion
   — 10000 atoms, один match — 10 мс. Невалидное сохранённое выражение
   пропускается, не скрывая raw-ошибку.
5. Каждое совпавшее правило создаёт отдельный `DiagnosticEvent`. Это не модель
   «первое правило победило»: несколько правил могут создать несколько событий
   для одного raw-сигнала.
6. Совпавшие units вычитаются из raw-дерева. Несовпавшие остатки возвращаются как
   неизвестные события, включая неизвестных siblings частично распознанного
   объекта.

События сортируются по `critical → warning → info`, затем по `sort_order`, после
этого известные идут перед неизвестными, а оставшиеся tie-breakers обеспечивают
стабильный порядок. ID события — SHA-256 от rule identity, типизированного пути и
raw value. Индексы элементов развёрнутой коллекции не входят в identity, поэтому
одна и та же ошибка сохраняет выбор при перемещении внутри массива; одинаковые
дубликаты схлопываются.

Известное событие возвращает frontend:

```json
{
  "id": "<stable sha256>",
  "rule_id": 42,
  "source_path": "errors",
  "source_segments": ["errors"],
  "raw_value": "WHEEL_BLOCKED",
  "title": "Заблокировано колесо",
  "description": "Проверьте колесо и привод",
  "severity": "critical",
  "sort_order": 7,
  "part": "переднее левое колесо",
  "view": "front",
  "x": 0.25,
  "y": 0.75,
  "indicator": "point"
}
```

Неизвестное событие получает `rule_id=null`, severity `warning`, описание
«Неизвестная ошибка. Правило диагностики не найдено» и не имеет локализации
`part/view/x/y/indicator`.

#### Как разметка показывается в UI

Frontend считает событие локализованным, только если одновременно присутствуют:

- `rule_id`;
- непустой `part`;
- допустимый `view`;
- конечные `x` и `y` в диапазоне 0–1;
- допустимый `indicator`.

Локализованные события рисуются поверх соответствующей фотографии робота.
Координаты переводятся в проценты ширины/высоты изображения, а маркер получает
форму из `indicator` и цвет из `severity`. Кнопка «Показать ошибку» выбирает
первое локализованное событие по severity, `sort_order` и стабильному ID и
переключает схему на `preferred_view`. В деталях показываются title, severity,
description, part и исходный `raw_value`. Неизвестные события остаются в списке
ошибок и деталях, но маркер на фотографии для них не рисуется.

`wheelsBroken` существует рядом с этой системой отдельно: parser превращает его
в `wheels_fault`, а UI использует заранее заданные hotspots шести колёс. Это не
замена diagnostic rule и не использует `x/y` из таблицы правил.

#### Inbox неизвестных ошибок

При выдаче snapshot неизвестные события поступают в
`diagnostic_unknowns.capture_unknowns`:

- сохраняется точный ограниченный unit и типизированный source path, но не полный
  upstream snapshot;
- один robot/error учитывается не чаще раза в 60 секунд;
- суммарный sample больше 8192 bytes пропускается;
- значения и пути с признаками password/token/cookie/authorization/API key не
  сохраняются;
- состояния записи: `new`, `ignored`, `mapped`;
- администратор может проигнорировать, вернуть в работу или классифицировать
  unknown созданием нового правила;
- классификация разрешена только если новое правило реально совпадает с
  сохранённым sample и не оставляет в нём непокрытого raw fault.

Управление выполняется через `/admin/diagnostic-rules` (list/create/reorder,
preview, test samples, update, disable) и `/admin/diagnostic-unknowns`
(list/detail/classify/ignore/reopen). Каталог правил имеет ETag; reorder требует
`If-Match`, чтобы не перезаписать параллельное изменение.

#### Что обязательно сделать с разметкой при смене API

1. До cutover выгрузить каталог правил из `GET /admin/diagnostic-rules` и inbox
   неизвестных ошибок без секретных raw values.
2. Для каждого `source_path` определить новый путь либо provider mapping в старый
   canonical path.
3. Для каждого `exact`-правила проверить, не поменялись ли регистр, whitespace,
   тип (`"1"` против `1`) и структура объекта.
4. Прогнать `preview` и `test-samples` на преобразованных fixtures.
5. Сравнить старые и новые `DiagnosticEvent`: title/description/severity/part,
   marker view и координаты должны сохраниться.
6. Проверить частично классифицированные объекты: новый mapping не должен терять
   неизвестных siblings.
7. Решить, должны ли event IDs сохраняться между provider. Если да, adapter должен
   выдавать тот же canonical raw value и source path; иначе UI selection будет
   сбрасываться при первом polling после cutover.
8. После переключения контролировать рост `new` unknowns: резкий рост означает
   несовместимость mapping, а не обязательно появление новых поломок роботов.

### 4.3 Динамические разделы Emergency

Начальная конфигурация лежит в `apps/api/data/emergency_sections.json`, но после
миграции БД source of truth — таблицы `emergency_sections`, `emergency_fields` и
`emergency_section_roles`. Администратор может добавлять произвольные dotted
paths. Endpoint `/admin/emergency/export` экспортирует фактическую конфигурацию.

Seed-конфигурация использует следующие поля:

| Раздел | Upstream paths |
| --- | --- |
| `status` | `name`, `vin`, `roverName`, `isOnline`, `lifecycleStatus`, `profile`, `autoMode`, `sadrMode`, `emergencyState`, `emergencyStatusCode`, `blockageReason`, `isCharging`, `velocity`, `locksStatus`, `selectorStatus` |
| `position_route` | `position`, `referencePoint`, `routeName`, `hasSmoothRoute`, `currentRouteEnd` |
| `batteries` | `batteriesStatus.battery1`, `battery2`, `chargePercents`, `fuel`, `fuelLevel`, `voltageValue`, `isCharging` |
| `errors` | `panics`, `errors`, `lastCritNotification`, `lastErrorNotification`, `notifications` |
| `parktronics` | `parktronics` |
| `wheels` | `wheelsBroken`, `wheelsCurrent`, `robotHudData.wheelsCurrent` |
| `logs_disk` | `disk`, `onStopSnapshotCountPercent`, `logId` |
| `localization` | `robotHudData.localizationSensors`, `robotHudData.gnss`, `robotHudData.localizationMonitor`, `icp` |
| `control` | `robotHudData.controlStatus` |
| `hardware_hud` | `robotHudData.hardwareDiags`, `robotHudData.sirenStatus`, `robotHudData.soundState`, `lte` |
| `metadata` | `metadata`, `cloudInstance`, `featuresSupported` |
| `service_raw` | `sdcOptions`, `strmConfig`, `trajectory` и все остальные top-level поля |

Особенно важен `service_raw`: formatter
`fields_and_top_level_leftovers` показывает администраторам все top-level поля,
не покрытые явно настроенными paths. Поэтому полностью скрыть изменение raw schema
адаптером нельзя без решения, что должен видеть этот раздел.

### 4.4 Реестр роботов

`GET /robots` не вызывает upstream Emergency при cache miss. Он читает только
свежие payload из 2,5-секундного cache для той же credential identity и добавляет:

- `online`, `charge_percent`, `mode`, `connection`;
- количество явно сообщённых ошибок/неисправных колёс;
- фильтры online/offline/unknown/errors.

Без свежего payload значения остаются unknown, а response помечается `partial`.
После смены provider поведение cache-only enrichment должно сохраниться, иначе
просмотр списка роботов начнёт создавать массовую upstream-нагрузку.

## 5. Frontend-потребители локальной Emergency API

Frontend не обращается к `emergency.sdc.yandex-team.ru` напрямую. Все запросы
идут через API Robopark.

| Компонент/сценарий | Использование |
| --- | --- |
| `EmergencyViewer` | resolve, snapshot, выбранный section, карта, схема колёс, cookie-stale state |
| `RobotResolver`, `RobotPage` | resolve номера/VIN, переход в карточку робота |
| `RobotCheckWorkspace` | polling snapshot и выбранного section, диагностическая схема и события |
| `robotDetailData` | resolve + snapshot для карточки, рядом загружает Tracker-задачи |
| `robotHealth` | формирует критические findings из offline/error banner/wheels |
| `RobotRegistryList` | показывает cache-only телеметрию и количество ошибок |
| `LegacyEmergencyRedirect` | resolve старого `/emergency?q=...` и redirect в `/robots/{vin}/check` |
| `Admin` | сохранение/проверка cookie, статусы valid/invalid/unavailable |
| `AdminEmergencyConfig` | CRUD и export конфигурации разделов; upstream напрямую не вызывает |

Стабильные frontend-типы:

- `EmergencySection { id, title }`;
- `EmergencySectionDetail { id, title, fields[{label, lines[]}] }`;
- `EmergencySnapshot` с телеметрией и `diagnostic_events`;
- integration status с полями `emergency_cookie_*`.

Если provider adapter сохраняет эти локальные DTO и error details, основной
frontend менять не требуется. При смене credential type всё же желательно убрать
Emergency-specific названия из Admin UI и status DTO.

## 6. Состояние, side effects и эксплуатация

### 6.1 Platform settings

Интеграция использует ключи:

- `emergency_cookie` — секрет;
- `emergency_cookie_identity` — generation/identity активного секрета;
- `emergency_cookie_valid`;
- `emergency_cookie_status`: `unchecked | valid | invalid | unavailable`;
- `emergency_cookie_checked_at`;
- `emergency_cookie_checked_robot`;
- `emergency_keepalive_ring`;
- `emergency_keepalive_seed_vin`;
- `emergency_keepalive_last_ok_at`.

Identity защищает от race: завершившийся запрос старой cookie не может испортить
состояние новой. При переходе на token/API key этот механизм следует сохранить,
переименовав его в provider credential identity.

### 6.2 Автоматические отчёты

При подтверждённой auth-ошибке создаётся один открытый report вида
`emergency_cookie_stale` для admin/royal с заголовком «Emergency cookie протухла».
После успешной проверки report закрывается. Unique partial index не допускает
несколько открытых отчётов.

При новой auth-модели нужно обновить kind/title/body/UI либо сохранить старый kind
как deprecated compatibility alias.

### 6.3 Readiness

`/health/ready` считает integrations `ok`, если настроены Tracker token и
Emergency cookie, а `emergency_cookie_valid` не равен `false`. Это мягкий сигнал:
отсутствие/невалидность Emergency не переводит сам readiness endpoint в `503`,
если БД доступна.

### 6.4 TLS

`httpx` вызывается без `verify=False`, то есть проверка TLS включена. Контейнер
должен доверять цепочке сертификата нового внутреннего сервиса. В текущем рабочем
дереве Dockerfile устанавливает `YandexInternalRootCA.crt` в системное trust
store и задаёт `SSL_CERT_FILE=/etc/ssl/certs/ca-certificates.crt`. Для другой PKI
нужно обновить доверенный CA, не отключая TLS verification.

### 6.5 Что не реализовано сейчас

- конфигурируемый upstream base URL;
- provider interface;
- schema versioning;
- retry для сетевых ошибок/`5xx`;
- специальная обработка `404` и `429`;
- постоянный connection pool;
- метрики latency/status непосредственно для Emergency upstream;
- проверка соответствия VIN в ответе VIN из запроса.

## 7. Стратегии замены

### Вариант A — полностью совместимый drop-in

Новый сервис повторяет URL path, cookie auth и legacy JSON. Тогда достаточно
вынести base URL в настройку и переключить её.

Плюсы: минимальное изменение. Минусы: сохраняет старую схему, терминологию cookie
и скрытые зависимости от raw JSON. Подходит только при доказанной совместимости.

### Вариант B — provider adapter с legacy-canonical payload (рекомендуется)

Добавить transport/provider, который получает ответ новой API и преобразует его
в ожидаемый Robopark JSON. Cache, snapshot, sections и frontend продолжают читать
каноническую форму.

Рекомендуемая граница:

```python
class RobotDiagnosticsProvider(Protocol):
    def fetch_robot_payload(self, *, credential: str, vin: str) -> dict[str, Any]: ...
```

Provider отвечает за:

- base URL, path и headers;
- auth и refresh;
- timeout/retry/rate-limit policy;
- перевод provider-specific ошибок в `AuthError`, `NotFound`, `RateLimited`,
  `Unavailable`, `InvalidResponse`;
- валидацию ответа;
- mapping в канонический payload Robopark.

Плюсы: локализует миграцию, сохраняет UI и бизнес-логику, позволяет feature flag
и rollback. Минусы: mapping должен охватить динамические paths и `service_raw`.

### Вариант C — новая каноническая доменная модель

Заменить raw payload типизированной моделью и перевести snapshot, sections,
diagnostic rules и frontend на неё.

Плюсы: чистый долгосрочный контракт. Минусы: самый большой объём, требует
миграции сохранённых section paths/rules и отдельного решения для raw viewer.
Имеет смысл после успешного перехода через вариант B.

## 8. Обязательный compatibility checklist новой API

Перед реализацией для новой API нужно заполнить таблицу:

| Проверка | Текущий Emergency | Новая API / решение |
| --- | --- | --- |
| Идентификатор робота | `YASADR` + 11 цифр | |
| Метод/path | `GET /api/v1/{vin}/` | |
| Auth | browser cookie | |
| Обновление credential | вручную в Admin | |
| Успешный root type | JSON object | |
| Not found | сейчас превращается в unavailable | |
| Unauthorized/forbidden | `401/403` или login HTML | |
| Rate limit | специально не обработан | |
| Timeout | 30 секунд | |
| TLS CA | системный trust store + internal CA | |
| Online/status | `isOnline` | |
| Скорость | `velocity` | |
| Батареи | `batteriesStatus.*` | |
| Позиция/heading | `position.*`, `robotHudData.heading` | |
| Режим | `autoMode/sadrMode/lifecycleStatus/profile` | |
| Связь | `lte`, wired aliases | |
| Диск | `disk/diskUsage` | |
| Ошибки | пять стандартных sources + DB paths | |
| Колёса | `wheelsBroken` | |
| Raw/admin fields | DB section paths + leftovers | |
| Допустимый polling | 10 секунд на видимую карточку | |
| Keep-alive | до 20 VIN каждые 90–120 секунд | |

Пустой пункт означает блокер для безопасного cutover, а не разрешение сделать
предположение.

## 9. Рекомендуемый план реализации

### Этап 0. Снять production-контракт

1. Экспортировать `/admin/emergency/export` из целевой инсталляции.
2. Экспортировать active diagnostic rules и их `source_path` без секретов.
3. Собрать обезличенные примеры payload для разных поколений роботов и состояний:
   online, offline, charging, wheel fault, panic/error, отсутствующие батареи,
   отсутствующая позиция.
4. Зафиксировать status codes, content types, latency и rate limits новой API.
5. Не сохранять cookie/token и чувствительные координаты в репозитории.

### Этап 1. Ввести seam без смены поведения

1. Добавить provider interface и legacy implementation вокруг текущего клиента.
2. Вынести provider/base URL в конфигурацию с текущим Emergency как default.
3. Оставить `emergency_client.fetch_robot_payload` временным compatibility facade,
   чтобы существующие тесты и monkeypatch-точки продолжили работать.
4. Включить provider name и credential identity в cache/live-merge key.
5. Добавить typed error taxonomy, сохранив текущие local HTTP details.

### Этап 2. Реализовать новую API и mapping

1. Реализовать auth и transport новой API.
2. Валидировать root schema и requested robot identity.
3. Преобразовать ответ в канонический payload.
4. Сопоставить все production section paths и diagnostic rule paths.
5. Определить политику `service_raw`: legacy projection, новый raw payload или два
   раздельных admin-раздела.
6. Добавить обработку not-found, `429`/`Retry-After` и refresh credential, если
   они существуют в новой API.

### Этап 3. Shadow verification

Для read-only диагностики можно временно читать оба provider:

- пользователю продолжать отдавать legacy результат;
- новый результат не писать в основной cache;
- сравнивать только нормализованные несекретные поля;
- не логировать raw payload, cookie/token, координаты и диагностические тексты;
- считать missing/mismatched fields и latency агрегировано.

Shadow read нельзя включать, если он нарушит rate limit или условия доступа новой
API.

### Этап 4. Cutover

1. Переключить небольшой контролируемый environment/instance.
2. Очистить in-process и live-merge cache.
3. Проверить Admin credential probe.
4. Проверить resolve, snapshot и каждый section минимум на двух VIN.
5. Проверить диагностические правила и unknown capture.
6. Проверить registry: отсутствие массовых запросов при cache miss.
7. Проверить keep-alive, status metadata, stale-credential report и readiness.
8. Проверить browser polling, hidden/offline pause и backoff.
9. После стабильного периода переключить остальные инсталляции.

### Этап 5. Очистка терминологии

После завершения rollback window:

- переименовать `emergency_cookie_*` в provider-neutral credential fields;
- мигрировать platform settings и report kind;
- обновить Admin UI и пользовательские сообщения;
- удалить compatibility facade и legacy provider;
- при необходимости заменить raw JSON канонической typed model.

## 10. Изменения по файлам

Минимальный рекомендуемый набор:

| Файл/область | Изменение |
| --- | --- |
| `services/emergency_client.py` | превратить в facade/provider transport; убрать hardcoded URL |
| `config.py` | provider и base URL, при необходимости timeout/retry settings |
| новый `services/robot_diagnostics_provider.py` | interface, errors, provider selection |
| новый provider module | auth, HTTP и mapping новой API |
| `services/emergency_cache.py` | provider-aware key, error taxonomy, invalidation |
| `services/platform_settings.py` | credential model/identity/status migration |
| `routers/admin_settings.py`, `schemas.py` | настройка и проверка нового credential |
| `services/emergency_keepalive.py` | token refresh/health semantics и rate limits |
| `services/emergency_snapshot.py` | менять только если adapter не выдаёт legacy-canonical payload |
| `services/emergency_sections.py` и DB config | mapping/migration динамических paths |
| `services/diagnostic_rules.py` | mapping/migration diagnostic source paths |
| `services/reports.py`, model/index migration | provider-neutral stale credential report |
| `routers/health.py` | provider-neutral integration readiness |
| `apps/web/src/api.ts` | только credential/status naming либо новый local DTO |
| Admin и Emergency/Robot UI | тексты и новые error states; телеметрию можно сохранить |
| Dockerfile/deploy | CA, DNS/proxy/network access нового endpoint |

## 11. Проверки, без которых замену нельзя считать готовой

### Transport contract

- правильный URL/method/headers;
- trailing slash policy;
- auth success, expired credential и refresh;
- `404`, `429`, `5xx`, timeout, DNS, TLS, redirect, HTML/non-JSON;
- JSON array/scalar отклоняются;
- VIN ответа соответствует запросу;
- секреты отсутствуют в exceptions и logs.

### Mapping contract

- golden fixtures старой и новой API дают одинаковый `EmergencySnapshotOut`;
- покрыты все поля таблицы snapshot;
- покрыты фактические DB section paths;
- покрыты фактические diagnostic rule paths;
- `service_raw` имеет согласованное поведение;
- отсутствующие поля дают `null/нет данных`, а не ложный healthy status.

### Cache/concurrency

- один upstream call на `(provider, credential identity, VIN)` в пределах TTL;
- разные VIN не блокируют друг друга;
- несколько процессов делят результат;
- смена credential/provider не принимает старый flight result;
- auth error корректно освобождает всех waiters;
- maintenance не записывает stale side effects.

### End-to-end

- все `/emergency/*` и legacy endpoints;
- robot page/check/workspace и Tracker health check;
- registry cache-only enrichment;
- Admin save/check/status;
- keep-alive ring;
- stale credential report lifecycle;
- readiness;
- роли и VIN scope;
- 10-секундный polling с backoff и сохранением last-known snapshot.

## 12. Rollback

Rollback должен быть переключением provider/config, а не обратным изменением
нескольких потребителей.

1. Сохранить legacy provider на время окна отката.
2. Не перезаписывать старую cookie новым credential; хранить их раздельно.
3. При откате увеличить credential identity/generation.
4. Очистить in-process и live-merge caches.
5. Проверить один probe VIN и status metadata.
6. Отключить shadow traffic, если именно он создаёт нагрузку/ошибки.
7. Не откатывать DB paths/rules до подтверждения, какой canonical payload активен.

## 13. Известные расхождения документации репозитория

README в разделе Phase 6 устарел относительно текущего кода:

- README говорит о cache TTL 5 секунд, фактически `PAYLOAD_CACHE_TTL_SECONDS = 2.5`;
- README говорит, что Emergency pages не используют browser timers, но текущий
  frontend обновляет snapshot и выбранный section с базовым интервалом 10 секунд.

Актуальное описание polling находится в `docs/automatic-data-refresh.md`.
При следующем изменении README эти две формулировки нужно синхронизировать.

## 14. Источники в репозитории

Основные файлы:

- `apps/api/src/robopark_api/services/emergency_client.py`;
- `apps/api/src/robopark_api/services/emergency_cache.py`;
- `apps/api/src/robopark_api/services/emergency_snapshot.py`;
- `apps/api/src/robopark_api/services/emergency_sections.py`;
- `apps/api/src/robopark_api/services/emergency_config.py`;
- `apps/api/src/robopark_api/services/emergency_scope.py`;
- `apps/api/src/robopark_api/services/emergency_keepalive.py`;
- `apps/api/src/robopark_api/services/platform_settings.py`;
- `apps/api/src/robopark_api/services/diagnostic_rules.py`;
- `apps/api/src/robopark_api/services/diagnostic_unknowns.py`;
- `apps/api/src/robopark_api/services/robot_registry.py`;
- `apps/api/src/robopark_api/routers/emergency.py`;
- `apps/api/src/robopark_api/routers/mechanic_emergency.py`;
- `apps/api/src/robopark_api/routers/admin_settings.py`;
- `apps/api/src/robopark_api/routers/admin_emergency.py`;
- `apps/api/data/emergency_sections.json`;
- `apps/web/src/api.ts`;
- `apps/web/src/components/emergency/EmergencyViewer.tsx`;
- `apps/web/src/domains/robots/RobotCheckWorkspace.tsx`;
- `apps/web/src/domains/robots/robotDetailData.ts`;
- `apps/web/src/components/tracker/robotHealth.ts`;
- `apps/web/src/pages/Admin.tsx`;
- `docs/automatic-data-refresh.md`.
