# Технический и продуктовый аудит Codex

- Дата: 2026-09-19
- Ветка: `ai/codex-platform`
- Исходный commit: `326964e96137cc29344bded9d60b67a7664b6022`
- Версия: `0.1.42`

## Охват и ограничения

Проверены API, web-интерфейсы, RBAC и изоляция парков, Tracker workflow,
Emergency, склад, миграции, фоновые задачи, вложения, snapshot/restore/OTA,
host installer, кеши и нагрузочный стенд. Внешние Tracker/Emergency, реальный
ARM-хост, Tuna и пользовательские данные не изменялись.

Локальная нагрузка подтверждает поведение приложения и SQLite на двух
процессах. Она не доказывает пропускную способность Wi-Fi, Tuna, ARM CPU или
реальных upstream. Для них оставлена отдельная приёмка в `REMAINING.md`.

## Итог

- Подтверждённых P0 не найдено.
- Все найденные P1 устранены.
- Устранены наиболее рискованные P2 вокруг межпроцессного кеша, Tracker
  history, аварийного экрана и деградации списка парков.
- Canonical gates полностью проходят: API `1750 passed, 1 skipped`, web
  `2034 passed`, host `685 passed`, installer `52 passed`, Docker images
  собраны и проверены.
- 200-session loopback benchmark прошёл все acceptance criteria.
- Signed OTA pack/verify round-trip прошёл с временной Ed25519-парой.

## P1 — исправлено

### P1-01: API/CI gate был красным

`ruff format --check` отклонял 19 файлов и не допускал pytest. Форматирование
приведено к pinned-конфигурации без изменения контрактов (`44cf661`).

### P1-02: OTA/restore upload мог резервировать сотни MiB лишней памяти

Первоначальный код собирал chunks и затем вторую копию через `join`. Первый
фикс `30bebf4` убрал двойную копию, но независимое ревью выявило, что
`read(max_bytes + 1)` у `BufferedReader` способен заранее резервировать почти
весь лимит даже для маленького файла. Финальный фикс `1b77fcb` сначала измеряет
seekable spool, отклоняет превышение и только затем читает фактический объём.
Disk-backed memory regression и `66` ops/restore/bridge тестов проходят.

### P1-03: runtime-версия OTA отставала от релиза

`APP_VERSION=0.1.40` расходился с авторитетной `0.1.42`, из-за чего два host
release-теста падали. Версии синхронизированы (`71bc4a7`).

### P1-04: 200 пользователей умножали Tracker history и получали таймауты

Реалистичный стенд показал `192` history-вызова и `11` таймаутов при холодном
чтении. Причина — статусная история обходила межпроцессный single-flight.
Добавлена JSON-safe shared history projection и bounded cache (`f4a121c`,
`6e2c544`). В финальном прогоне history загружается один раз на issue.

### P1-05: список парков выглядел пустым при кратком обрыве связи

Ошибка fleet API заменяла уже разрешённые парки пустым списком, создавая
эффект потери данных. Теперь UI сохраняет последний разрешённый scope,
показывает заметное предупреждение и кнопку повтора, не скрывая рабочий экран
(`2199185`).

## P2 — исправлено

### Правдивые Tracker capabilities

UI больше не рекламирует retired `assign/unassign/transition`, а `close`
доступен только review-ролям при pending review (`3ed3dd0`).

### Надёжный cross-worker single-flight

Shared payload не сериализует SDK resource, но leader сохраняет его локально;
follower восстанавливает SLA/history безопасным путём (`7362c2d`, `f1784cc`).
Устаревший leader не может перезаписать invalidation (`8f43911`), а exact,
prefix и namespace clear сериализованы общим guard-lock без рекурсивного
retry (`8958a6c`).

### Один запрос на аварийный экран вместо двух

Добавлен `GET /emergency/{vin}/view?section=...`, который за одну scope-check и
одну загрузку payload возвращает snapshot и активный раздел. Web переведён на
новый маршрут; legacy endpoints сохранены (`0485cab`). Это уменьшает ingress и
работу API на каждый poll при нестабильном Wi-Fi.

### Надёжный нагрузочный CLI

`capacity_benchmark.py --help` падал из-за `%` в argparse help. Добавлен
регрессионный тест и исправлено экранирование (`b49520e`).

## Оставшиеся продуктовые решения

- GET карточки Tracker синхронно reconciles внешнее закрытие и делает локальную
  запись. Перенос в background требует решения о допустимой eventual
  consistency.
- `InventoryMovement.idempotency_key` глобально уникален. Это безопасно, но
  контракт надо документировать либо менять на park-scoped отдельной миграцией.
- React lint остаётся зелёным с предупреждениями по refs, dependency arrays,
  `Date.now()` во время render и mixed exports. Они вынесены в плановую работу.

## Проверенные защитные механизмы

- Fail-closed RBAC, Tracker scope и изоляция парков.
- Idempotency/lease/retry для Tracker actions и outbox.
- Атомарное конкурентное списание склада.
- Защита вложений от traversal, превышений MIME/size и неполного cleanup.
- SQLite WAL/busy timeout/foreign keys и bounded caches.
- Timeout/process-group cleanup внешних процессов.
- ZIP path/member/expanded-size/ratio/checksum/signature validation.
- OTA rollback/recovery, systemd hardening и retention.
