# Чистая базовая версия Robopark

Статус: согласовано в чате 2026-09-19. Выпуск предназначен для
чистой локальной переустановки; данные текущей SQLite не переносятся.

## 1. Цель

Собрать одну стабильную базовую версию для Khadas VIM4 (8 ГБ) и
Jetson AGX Orin (32/64 ГБ): PostgreSQL, быстрый кэш, ограниченное
хранение, два целостных интерфейса и полную ролевую паритетность.

Нельзя объявлять версию готовой по числу unit/E2E-тестов. Приёмка
включает визуальное сравнение, реальные циклы ролей, длительный
прогон памяти и нагрузку 200 сессий.

## 2. База данных и чистая установка

- Production-БД — PostgreSQL 17 в отдельном контейнере на том же хосте.
- SQLite остаётся только для unit-тестов и лёгкого dev-режима.
- Установщик не импортирует старую SQLite. Перед удалением старой
  установки он явно показывает цель и требует локального подтверждения.
- Схема, partial indexes, boolean defaults, upsert, row locking и очереди
  должны проходить отдельный PostgreSQL integration suite.
- Snapshot/restore/OTA не читают файл SQLite: используют
  `pg_dump --format=custom`, `pg_restore`, проверку Alembic head и smoke.
- Релиз жёстко фиксирует major-версию PostgreSQL и формат backup.
- Пул соединений, API workers, PostgreSQL memory и cache budgets
  рассчитываются по CPU/RAM с безопасным профилем для 8 ГБ.

## 3. Кэш у пользователя

Три уровня: React/store memory L1, account-scoped IndexedDB L2,
версионная PWA-оболочка. При открытии сначала показываются
последние данные, затем идёт фоновая синхронизация.

- Ключ включает user, permission fingerprint, park, resource, query и schema.
- Logout, 401/403, смена роли/парка очищают недоступный scope.
- Change feed точечно инвалидирует объекты; polling остаётся резервом.
- Мутации оптимистично меняют UI, затем сверяются с сервером;
  idempotency key не даёт повторить действие.
- Фото не дублируются безгранично: миниатюры и LRU-бюджет,
  оригинал по запросу.
- Размер L2 адаптивен к browser quota, но имеет жёсткий ceiling;
  очистка идёт по LRU/TTL.
- Service worker не перехватывает API и auth; старая оболочка
  удаляется после активации новой версии.

## 4. Кэш и нагрузка на сервере

- Bounded in-process L1 и общий межпроцессный L2 без Redis.
- Single-flight объединяет одинаковые холодные запросы между API workers.
- Stale-while-revalidate и bounded stale-if-error сохраняют последний
  достоверный ответ при кратком сбое Tracker/Wi-Fi.
- После мутации очищаются только затронутый issue, comments,
  counts и известные list projections; полная очистка всех Tracker-списков
  не допускается.
- API даёт ETag/Last-Modified для больших GET, а клиент использует
  conditional requests. Nginx не кэширует auth/API между пользователями.
- Каждый cache family имеет TTL, max entries, max bytes, hit/miss/eviction,
  refresh latency и причину инвалидации.

## 5. Хранение и автоочистка

Автоочистка затрагивает локальные копии, которые подтверждённо
доставлены в Tracker. Активные операции и недоставленные данные
никогда не удаляются по TTL.

| Категория | Политика |
| --- | --- |
| Оригиналы фото после подтверждённой отправки | 7 дней |
| Миниатюры и закрытые задачи/чат | 30 дней, LRU |
| Завершённые репорты | 30 дней |
| Детальная аналитика | 90 дней, затем агрегаты |
| Журналы | 14 дней и 256 МБ суммарно |
| Диагностика, tmp, failed upload | 7 дней |
| OTA, rollback, Docker image/build cache | bounded host retention; current + previous + verified recovery защищены |

Первичные локальные данные — users/roles/parks, settings, inventory balances
и незавершённые documents, diagnostic rules — не удаляются.
Они не имеют полной копии в Tracker.

Хост хранит не менее `max(15% раздела, 6 GiB)` свободными.
Порядок давления: cache/tmp -> diagnostics/logs -> подтверждённые
Tracker copies. Не применяется общий `docker system prune`.

## 6. Аппаратные возможности

Возможности определяются probe-ами, а не одним именем платы.

- VIM4: hardware JPEG/GStreamer для миниатюр; NPU только на New VIM4.
- Orin: NVJPEG/NVENC/NVDEC; CUDA/TensorRT/DLA только для будущих
  image/vision задач, не для JSON/HTTP/SQL.
- БД, uploads и server cache помещаются на NVMe, если он безопасно
  обнаружен при установке.
- Отсутствие GPU/NPU/plugin/device всегда даёт software fallback.
- В UI host health показываются выбранный profile, acceleration,
  cache/storage budgets и последняя уборка.

## 7. Два интерфейса

Оба режима имеют один API, route state, resource cache, drafts, files,
camera state и permissions. Различаются presentation/layout, а не бизнес-логика.
Общие CSS rules ограничиваются reset/tokens/accessibility; структурные
правила Classic и A не могут перезаписывать друг друга.

### 7.1. Новый A: эталон композиции

Скриншот, переданный пользователем 2026-09-19 19:54:53,
— источник истины для task-first desktop, а не иллюстрация цветов.

- Слева узкая рабочая навигация: park, overview, work count, robots,
  inventory, campaigns; профиль/смена снизу.
- В центре одна рабочая последовательность, а не стек card-in-card:
  task header, Repair/Check/Chat, current steps, comment/photo/shift handoff.
- Справа context rail: robot now, errors, LTE/two batteries, last check,
  operator presence/chat. Он не дублирует центральную форму.
- Главное действие находится в нижней action bar рабочей области.
- Самодостаточные страницы (склад, reports, admin, analytics) сохраняют
  ту же shell density, type scale, lines и action hierarchy; они не перекрашенный Classic.
- На 320–412 px left rail становится нижней навигацией, context rail
  переходит в компактный disclosure после task header, а primary action
  остаётся sticky над навигацией/клавиатурой.

Текущая реализация A не считается baseline: браузерная проверка
показала, что она сохраняет Classic shell, ломает текст карточек
очереди на desktop и не даёт обещанной трёхзонной композиции.

### 7.2. Classic

Classic восстанавливается как цельная спокойная система, а не
как fallback из смешанных legacy/new CSS. Его эталон — согласованный
«последний в чате» вариант и последняя согласованная компоновка
проверки робота. Общие functional fixes применяются к обоим режимам.

## 8. Исправления, выявленные браузером

Как минимум в план входят:

- пересечение/перенос текста в A overview queue cards;
- обрезанные подписи mobile bottom navigation;
- видимые технические надписи `Свернуть: ...`;
- сырая Tracker-разметка `<[`, `<{` в описании задачи;
- warning без контейнера и неровные отступы task detail;
- дублирование VIN в recent robots;
- одинаковая композиция robot check в A и Classic;
- несогласованные breakpoints/gutters и двойные page paddings;
- полная ролевая паритетность admin/royal, mechanic, operator, driver,
  custom role во всех routes, tabs, dialogs и denied states.

## 9. Приёмка

1. Visual structure tests: A task desktop сравнивается с эталоном
   по зонам, порядку, action hierarchy, widths/gaps/type scale; тест
   «нет overflow» не заменяет эту приёмку.
2. Screenshot matrix: 320/390/412/899/1440, light/dark, Classic/A, representative
   routes и roles. Репрезентативные снимки просматриваются человеком.
3. Route/role/state matrix охватывает не только URLs, но и вложенные
   tabs, forms, file/camera, empty/error/stale/denied и полные циклы ролей.
4. PostgreSQL integration suite и чистая install/backup/restore/reinstall на
   ARM64-стенде; SQLite suite не считается доказательством production.
5. 200 изолированных сессий: warm/cold, read/write mix, Wi-Fi latency/loss,
   p50/p95/p99, RPS, errors, upstream calls, cache hits, RSS/CPU/disk/network.
6. Soak: не менее 8 часов для API/background jobs и повторяемые UI cycles;
   bounded cache/storage/timers/subscriptions/object URLs/media tracks.
7. Cleanup pressure tests доказывают, что current/previous/recovery,
   unconfirmed uploads/actions и primary data не удаляются.
8. Физическая проверка OnePlus: PWA install/update, camera/file fallback, keyboard,
   412 px, light/dark, 50 navigation/action cycles.

## 10. Выпуск

Конечный артефакт — чистый установочный архив, который может ставиться
на Armbian 26/VIM4 и Ubuntu 22/Orin. В payload нет прошлых OTA, diagnostics,
dev/test данных и SQLite-базы. Функция будущих обновлений остаётся.

Архив не выпускается, пока не закрыт каждый пунк приёмки либо
не записано явное неснятое ограничение.
