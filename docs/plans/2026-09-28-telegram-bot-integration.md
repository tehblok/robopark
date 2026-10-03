# Telegram-бот в Robopark: план реализации

**Цель:** включить существующего бота в чистую установку Robopark как
отдельный полностью отключаемый сервис, сохранив доступные данные и
переведя Tracker-трафик на API Robopark.

**Архитектура:** переходный bot image с отдельным data-каталогом; royal
управляет состоянием через API и host bridge. API хранит Telegram token
зашифрованным и выполняет все запросы к Tracker. План следует
[спецификации](../architecture/2026-09-28-telegram-bot-integration.md).

**Стек:** Python 3.12, FastAPI, PostgreSQL, Docker Compose, React/TypeScript,
короткие pytest/Vitest/Playwright сценарии.

## Общие ограничения

- Работать от текущего незакоммиченного `main`; не коммитить, не сбрасывать
  дерево, не менять четыре защищённых пользовательских артефакта.
- Не копировать личные JSON и токены в git или OTA. Исходный ZIP остаётся
  в Downloads; импорт запускается отдельно после установки.
- Bot disabled по умолчанию. Выключение не удаляет данные и токен.
- Не запускать одновременно старый и новый Telegram long-poll.
- Host lifecycle использует точные Robopark Compose-ресурсы; Docker volumes
  и широкий `docker system prune` запрещены.
- Каждый блок: воспроизведение или падающий короткий тест, минимальная
  правка, адресная проверка; ARM/Telegram приёмка отдельно.

## Проверки риска

1. ZIP с symlink/traversal/дубликатом: отказ до записи.
2. Повторный импорт: сохранение первого набора байтов без перезаписи.
3. Недоступный Tracker: бот показывает ошибку, не выдаёт пустой результат.
4. Отключение во время доставки: новый цикл не начинается, in-flight
   операция завершается или переносится с идемпотентностью.
5. OTA/rollback/перезагрузка при включённом и выключенном боте:
   сохранение точного желаемого состояния и каталога данных.

## Задачи

### 1. Импорт данных

**Файлы:** `apps/bot/robopark_bot_import.py`,
`tests/bot/test_bot_import.py`.

- [x] Добавить `preview_bot_export(path)` и
  `import_bot_export(path, destination)` для восьми экспортированных JSON.
- [x] Сначала проверить опасные ZIP и отсутствие перезаписи падающими
  тестами, затем атомарную запись и fsync.
- [ ] Независимо проверить тесты, прочность проверки symlink и preview
  настоящего ZIP без печати данных.

### 2. Ограниченный внутренний Tracker gateway

**Файлы:** `apps/api/src/robopark_api/routers/internal_bot.py`,
`apps/api/src/robopark_api/services/bot_tracker.py`,
`apps/api/src/robopark_api/main.py`, целевые API-тесты.

- [ ] Тест: без service key — 401; ключ не появляется в ответе/логе.
- [ ] Тест: ограниченные search/issue/links/changelog используют
  `platform_settings.get_tracker_token` и существующий Tracker client/cache.
- [ ] Тест: лимиты запроса, очередей, страниц, времени и размера ответа;
  ошибка интеграции явно возвращается как ошибка, не пустой список.
- [ ] Реализовать четыре JSON-операции с узким DTO для старого бота.

### 3. Telegram token, настройки и импорт через веб

**Файлы:** `services/platform_settings.py`, `routers/admin_bot.py`,
`services/audit.py`, `schemas.py`, тесты API, `SystemPage` или отдельная
owner-страница и её тесты.

- [ ] Тест: только royal задаёт/ротирует Telegram token, видит маску и
  preview ZIP; ни один ответ не раскрывает секрет.
- [ ] Тест: импорт разрешён только когда бот выключен, не затрагивает
  существующие данные, показывает восемь файлов и результат проверки.
- [ ] Реализовать owner UI для настроек, статуса и подтверждённых
  enable/disable; визуально проверить ПК и телефон.

### 4. Runtime бота

**Файлы:** `apps/bot/legacy/*`, `apps/bot/Dockerfile`,
`apps/bot/bridge_client.py`, `deploy/docker-compose.yml`, bot-тесты.

- [ ] Перенести проверенные файлы кода из пользовательского архива без
  runtime JSON, секретов, old OTA/install и лишнего vendor.
- [ ] Тест: четыре метода `tracker_api` используют только Robopark API;
  отсутствие внутренних credentials не вызывает прямой запрос в Tracker.
- [ ] Тест: Telegram dispatch, PNG, расписания, роли, паузы и callbacks
  на данных-копиях; старые shell host/OTA действия не выполняются.
- [ ] Отдельный образ и ограниченный контейнер; volume содержит только
  `data/telegram-bot`, no Docker socket, no public port.

### 5. Полное включение/отключение и OTA

**Файлы:** host command types/effects, `deploy/compose_secrets.py`,
`deploy/systemd/robopark.service`, `deploy/host/robopark_host/ota_update.py`,
`updater.py`, image retention и целевые host-тесты.

- [ ] Тесты start/stop только точного `bot` сервиса, выключения без
  удаления данных, отказов и идемпотентных повторов.
- [ ] Тесты reboot, две последовательные OTA, ошибка/обрыв и rollback
  при обоих состояниях бота; current + rollback bot images сохраняются.
- [ ] Добавить root-owned persisted desired state, независимое восстановление
  после reboot, bounded image/BuildKit retention для bot image.

### 6. Сквозная приёмка

- [ ] Архив чистой установки включает код, образ и инструкцию импорта,
  но не содержит ZIP пользователя и credentials.
- [ ] Локальная интеграция: импорт, royal toggle, поиск тикета через API,
  Telegram send в тестовом чате и повторный запуск.
- [ ] После доступа к ARM-хосту: старый бот остановлен, новый token задан
  владельцем, проверены реальные отправка/получение, reboot и rollback.
- [ ] Зафиксировать размеры/пути перед любыми очистками старой установки;
  без разрешения не удалять её.

Ни один пункт плана не подразумевает commit, merge, установку на реальном
хосте или удаление данных без отдельного явного разрешения.
