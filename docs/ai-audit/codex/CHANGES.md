# Изменения по аудиту Codex

## Надёжность и безопасность

- `30bebf4`, `1b77fcb` — bounded-memory ops upload: фактический размер
  seekable spool проверяется до чтения, маленький архив не резервирует лимит
  `512 MiB`.
- `71bc4a7` — runtime/snapshot/OTA version синхронизирована с `0.1.42`.
- `44cf661` — восстановлен обязательный API format gate.
- `3ed3dd0` — Tracker capabilities отражают реально исполнимые действия.

## 200+ одновременных пользователей

- `7362c2d` — восстановлен JSON-safe cross-worker Tracker single-flight.
- `f1784cc` — follower не теряет SLA, если shared issue не содержит SDK
  resource.
- `8f43911` — superseded leader не публикует устаревший cache result.
- `8958a6c` — exact/prefix/namespace invalidation сериализованы guard-lock;
  retry ограничен и итеративен.
- `f4a121c`, `6e2c544` — Tracker status history дедуплицируется между
  процессами и проверяется нагрузочным контрактом.
- `0485cab` — snapshot и активный Emergency section объединены в один API
  request при сохранении старых маршрутов.
- `b49520e` — исправлен и покрыт тестом CLI локального capacity benchmark.

## Интерфейс и отказоустойчивость

- `2199185` — при ошибке загрузки парков сохраняются разрешённые/последние
  данные; AppShell показывает stale-warning и retry без блокировки экрана.
- `6628cb6` — memory/plateau тест учитывает один namespace guard-lock.
- `55372ee` — web-регрессия закрепляет сохранение `park` в management links.

## Совместимость

Новых миграций, зависимостей и переменных окружения нет. Legacy Emergency
snapshot/section endpoints сохранены. Shared cache остаётся внутренним,
короткоживущим и совместимым через обычную invalidation/retention логику.
