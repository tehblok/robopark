# Robopark: стабилизация, усиление безопасности и переход на PostgreSQL + Redis

**Дата:** 2026-09-01

**Статус:** дизайн одобрен в рабочем диалоге; ожидает проверки письменной спецификации

**Тип:** umbrella architecture spec; implementation plan из этого документа охватывает только Этап 0

**Область:** API, web-клиент, хранение данных, общий runtime-state, обновление, backup/restore и CI

## 1. Контекст

Robopark — работающий модульный монолит на FastAPI и React, интегрированный с Tracker и Emergency. Проект уже содержит полезные доменные сценарии, RBAC, Alembic-миграции и тесты, поэтому полный rewrite создаст больше рисков, чем управляемая переработка существующей системы.

Аудит выявил несколько классов проблем:

- чтение Emergency snapshot/section возможно напрямую, в обход VIN scope-check;
- локальная SQLite имеет revision `0015_report_attachments`, отсутствующую в `main`;
- процесс обновления способен удалить живые `*.env`, не откатывает код вместе с данными и не ждёт readiness;
- замена SQLite-файла небезопасна при двух Uvicorn workers;
- frontend build/lint и три backend-теста сейчас красные, Ruff сообщает 20 ошибок;
- браузерный cache не всегда привязан к пользователю, правам и парку;
- межпроцессные locks/cache и login throttle реализованы через файлы или память отдельного worker;
- Python dependency graph и контейнерные артефакты воспроизводимы не полностью.

## 2. Цели

1. Закрыть подтверждённые обходы доступа и утечки данных между субъектами.
2. Вернуть воспроизводимый зелёный quality gate до инфраструктурного cutover.
3. Сделать PostgreSQL единственным источником постоянных данных.
4. Использовать Redis только как эфемерный coordination plane.
5. Перенести существующие SQLite-данные и вложения без потери и с проверяемым результатом.
6. Сделать update, backup, restore и rollback согласованными для кода и данных.
7. Улучшить границы модулей без изменения публичных REST-контрактов без необходимости.
8. Сохранить существующую схему публикации Tuna и локальный web endpoint `127.0.0.1:8080`.

## 3. Не-цели

- микросервисы, event bus, CQRS или отдельный BFF;
- dual-write SQLite/PostgreSQL или CDC;
- хранение авторизационных сессий, RBAC, аудита или доменных данных в Redis;
- multi-host HA и отдельный кластер очередей;
- полный визуальный redesign;
- удаление legacy endpoints до отдельного compatibility window;
- автоматический возврат к SQLite после того, как PostgreSQL принял новые записи.

## 4. Выбранный подход

Используется поэтапная схема **stabilize → secure → prepare → migrate → simplify**.

Она выбрана вместо двух альтернатив:

- немедленный platform-first cutover объединяет слишком много рисков в одном релизе при уже красных проверках;
- полный rewrite задерживает устранение известных уязвимостей и повторно реализует рабочие доменные сценарии.

Каждый этап заканчивается измеримым gate и может быть выпущен отдельно. В одной поставке не совмещаются необратимая миграция данных и массовая переработка интерфейса.

## 5. Целевая архитектура

```text
Tuna
  |
  v
Nginx / React web (127.0.0.1:8080)
  |
  v
FastAPI modular monolith
  |-- PostgreSQL: users, sessions, RBAC, parks, reports, audit, secrets
  |-- Redis: grants, throttle, cache, single-flight, leases, rate coordination
  |-- Tracker gateway
  `-- Emergency gateway
```

### 5.1 Границы модулей

```text
HTTP routers / web domain clients
              |
              v
feature application services
identity-access | parks | tracker | emergency | reports | ops
              |
              v
ports
repositories | TrackerGateway | EmergencyGateway | LiveCache
JobLease | SnapshotStore | Clock
              |
              v
SQLAlchemy/PostgreSQL | Redis | filesystem | upstream HTTP
```

Правила зависимостей:

- router валидирует HTTP-ввод и вызывает application service;
- application service не зависит от `Request` и не выполняет upstream HTTP напрямую;
- Tracker/Emergency payload преобразуется anti-corruption adapter-ом;
- кэшироваться может только raw upstream result, а ACL и фильтрация выполняются после чтения cache;
- один transport/error interceptor остаётся общим для web, domain clients разделяются по областям;
- публичные URL и схемы ответов сохраняются, кроме новых документированных кодов ошибок безопасности.

## 6. PostgreSQL как источник истины

PostgreSQL хранит все долговечные и транзакционные данные:

- пользователей, сессии, роли, индивидуальные разрешения и парки;
- audit events;
- reports, comments, workflow state и report attachments metadata;
- зашифрованные Tracker/Emergency credentials;
- durable ops jobs, их фазы и результаты, которые должны переживать restart.

Базовая deployment-версия — PostgreSQL 17 на внутренней Compose-сети. Образ фиксируется полным digest при реализации. Порт PostgreSQL на host не публикуется. API использует `postgresql+psycopg://` и ограниченный connection pool, рассчитанный на число Uvicorn workers.

Alembic остаётся единственным способом создать или обновить schema. `Base.metadata.create_all()` не используется для production schema. Миграции выполняются один раз до старта workers под PostgreSQL advisory lock.

### 6.1 Восстановление migration history

До перехода восстанавливается отсутствующий `0015_report_attachments` и соответствующий production-код attachment-функции. Источник найден в недостижимом commit `edea0280e18aaf12633eff3c94aa359ea7b7818f`.

Весь commit не переносится: он содержит runtime-артефакты. Выборочно восстанавливаются и отдельно проверяются migration, model/config/schema/router/service и релевантные тесты. После этого копия существующей SQLite обязана успешно разрешать `alembic current` как `0015_report_attachments`.

### 6.2 Диалектная совместимость

До первой PostgreSQL-инсталляции:

- SQLite-only boolean defaults в migrations `0003`, `0013`, `0014` заменяются на переносимые `sa.true()`/`sa.false()`;
- raw seed SQL с boolean literals `1/0` в `0013` заменяется диалектно корректными bound values/SQLAlchemy expressions;
- partial indexes в `0006` получают эквивалентные `postgresql_where` expressions;
- чистые SQLite и PostgreSQL проходят `alembic upgrade head`;
- schema parity проверяет tables, columns, types, nullability, defaults, indexes, unique constraints и foreign keys.

## 7. Redis как эфемерный coordination plane

Базовая deployment-версия — Redis 7.2 на внутренней Compose-сети, без опубликованного порта, RDB/AOF и persistent volume. Образ фиксируется digest. Доступ API ограничен password/ACL из host-managed environment; секрет не попадает в repository, logs или backup.

Каждый ключ имеет TTL. Ключи и аргументы, содержащие пользовательские идентификаторы, VIN или upstream identifiers, формируются через keyed HMAC. Политика `noeviction` сочетается с application-level лимитами размера и количества cache entries: security keys нельзя вытеснить cache flood-ом.

Redis хранит только:

- session-bound Emergency view grants;
- распределённые auth throttle counters;
- short-lived raw Tracker/Emergency cache;
- single-flight locks и upstream concurrency leases;
- renewable background-job leases;
- namespace generation для безопасной invalidation.

Redis никогда не хранит cookies, integration tokens, итоговое решение ACL, role-rendered payload или постоянную пользовательскую сессию.

### 7.1 Поведение при недоступности Redis

| Функция | Поведение |
|---|---|
| Login/registration throttle | `503 auth_rate_limiter_unavailable` до поиска пользователя и Argon2 |
| Emergency grant issue/check | `503 emergency_coordination_unavailable`; fail closed |
| Background job lease | Job не запускается или останавливается при потере lease |
| Shared raw cache | Cache bypass с bounded local concurrency; upstream error остаётся контролируемым |
| Single-flight | Ограниченное ожидание, затем bounded upstream call или явная ошибка |

Активные обычные сессии продолжают работать, если PostgreSQL доступна. Liveness не зависит от Redis; readiness требует PostgreSQL и сообщает Redis как degraded component. Deployment smoke test требует исправного Redis.

### 7.2 Locks и invalidation

Распределённый lock создаётся через `SET NX PX` со случайным owner token. Renew и release выполняются атомарным Lua-script только при совпадении owner. Потерянный лидер освобождается TTL.

При mutation/rotation namespace generation увеличивается. Leader пишет результат в cache только если generation не изменилась за время upstream call. Это исключает гонку, при которой старый fetch возвращает устаревшее значение после invalidation.

## 8. Emergency access grant

Подтверждённый bypass закрывается единым session-aware flow для новых и legacy endpoints.

До появления Redis-grant первый security commit вводит временную fail-closed защиту: snapshot и section для scope-limited ролей вызывают текущий `_enforce_vin_scope`. Это немедленно убирает прямой ACL bypass ценой повторной проверки через существующий короткий Tracker cache. После выпуска grants-модели временная проверка на каждом poll удаляется. Финальное поведение определяется следующими подразделами.

### 8.1 Открытие просмотра

`POST /emergency/resolve`:

1. аутентифицирует текущую `AuthSession` и пользователя;
2. нормализует VIN;
3. проверяет текущую локальную роль и активность;
4. для operator/mechanic выполняет свежий Tracker scope query без stale authorization cache; Redis используется только для single-flight одинаковых одновременных проверок;
5. получает Emergency metadata/sections;
6. только после полного успеха атомарно выдаёт grant сроком 10 минут.

Driver/admin/royal сохраняют существующие продуктовые исключения от Tracker scope, но также обязаны сначала получить grant. Прямой snapshot/section без resolve запрещён для всех ролей.

Ключ имеет форму:

```text
rp:v1:emg:grant:{session_hmac}:{vin_hmac}
```

`session_hmac` строится из `AuthSession.token_hash`, `vin_hmac` — из нормализованного VIN. В value находятся только schema version, `user_id` и issued-at; VIN, cookie и upstream secrets отсутствуют.

### 8.2 Чтение и закрытие

- каждый snapshot/section request выполняет обычную session/user validation и требует exact session+VIN grant;
- section-level RBAC продолжает применяться после grant;
- poll не обращается к Tracker;
- frontend выполняет новый resolve примерно за 30 секунд до expiry;
- best-effort close удаляет exact grant; пропущенный close безопасно компенсируется TTL;
- logout/password change/deleted session немедленно делают grant непригодным за счёт стандартной проверки сессии.

Отсутствующий или истёкший grant возвращает единый `403 emergency_view_not_open`, не раскрывая существование VIN или grant другой сессии. Максимальное окно после отзыва Tracker-доступа — остаток 10-минутного TTL.

## 9. Распределённый auth throttle

Текущая process-local map заменяется Redis Lua-операциями:

- общий IP token bucket проверяется до дорогой password hashing;
- client IP берётся из proxy header только от доверенной внутренней Nginx-сети; произвольный внешний `X-Forwarded-For` не считается доверенным;
- неверный пароль увеличивает atomic counter для `username_hmac + ip_hmac` и создаёт lock с TTL;
- ответ содержит корректный `Retry-After`;
- успешный login очищает failure/lock только для пары, но не общий IP budget;
- registration имеет отдельный IP budget;
- жёсткий username-only lock не используется, чтобы не дать атакующему блокировать известные аккаунты.

## 10. Browser data isolation

Resource layer становится auth-aware:

- query key содержит subject, `authz_version`, park/scope и фильтры;
- Tracker, Emergency, reports и admin payload по умолчанию хранятся только в памяти;
- login/logout/401 централизованно отменяют requests и очищают private cache;
- 403 или смена `authz_version` очищает scope и повторно валидирует `/auth/me`;
- при смене key старые данные не отображаются даже на один render;
- каждый loader принимает `AbortSignal`, а ответ сохраняется только при совпадении текущей scope/version;
- invalidation выполняется domain tags, а не несогласованными строковыми ключами;
- auth context переоценивается при focus/navigation и ограниченным polling на привилегированных экранах.

`authz_version` — постоянное non-null integer-поле пользователя с начальным значением `1`. Любая транзакция, меняющая role, active/access status, индивидуальные permissions или parks, атомарно увеличивает его через единый identity-access service. `/auth/me` возвращает `user_id` и текущий `authz_version`; web формирует subject scope как `user_id:authz_version`. Admin mutations и внутренние migration/ops-команды обязаны идти через тот же service или явно выполнить increment. Изменения, внесённые в БД в обход приложения, не считаются поддерживаемым способом администрирования.

Клиентская логика не заменяет server-side ACL. Отдельно исправляются `tracker_login` для self-assign и UI-проверка `mechanic_can_write`.

## 11. Перенос SQLite → PostgreSQL

Используется offline schema-first migration без dual-write.

### 11.1 Репетиция

1. Создать consistent SQLite snapshot через `sqlite3.Connection.backup`, а не копированием live DB/WAL.
2. Развернуть пустую PostgreSQL и выполнить Alembic до точного `head` release-кандидата; `0015_report_attachments` является обязательным восстановленным предком, но не фиксированной конечной revision cutover.
3. Перенести application tables SQLAlchemy Core batch inserts в явном FK-порядке; `alembic_version` не копировать.
4. Сохранить исходные primary keys.
5. Для `reports.parent_report_id` сначала вставить строки без self-reference, затем выполнить проверяемый update.
6. После copy выставить PostgreSQL sequences строго выше `MAX(id)`.
7. Legacy naive datetime трактовать как UTC и сравнивать как UTC instant.
8. Сохранить attachment files и `storage_key` без переименования.
9. Не менять `SECRET_KEY`: существующие credentials должны расшифровываться.

Проверки миграции:

- row count для каждой таблицы;
- множества/digest primary keys;
- null counts и domain invariants: уникальность permission assignments, допустимое число парков по роли, корректные report parent/workflow references и отсутствие сессий без пользователя;
- отсутствие orphan foreign keys;
- attachment metadata и checksums файлов;
- успешная расшифровка существующих integration credentials на staging copy;
- совпадение Alembic revision с зафиксированным `head` release-кандидата;
- новые sequence-generated IDs выше импортированных;
- RBAC samples для каждой роли/парка;
- audit write, report workflow, Tracker и Emergency smoke scenarios.

### 11.2 Production cutover

1. Запустить maintenance mode и остановить API/web writers и background jobs.
2. Создать два immutable артефакта: штатный SQLite snapshot и checksummed data/attachment archive.
3. Выполнить отрепетированный import и автоматическую parity-проверку.
4. Запустить новый stack в закрытом режиме и выполнить localhost/Tuna smoke tests.
5. Принять явное go/no-go решение до открытия writes.
6. После go открыть доступ; PostgreSQL становится единственным source of truth.

До открытия writes rollback возвращает прежний SQLite-compatible release и сохранённый volume, потому что writers всё ещё остановлены. После первой PostgreSQL-записи данные автоматически не откатываются: rollback переключает только предыдущий PostgreSQL-compatible code release, сохраняя текущую PostgreSQL и все новые записи. В observation window используются только backward-compatible expand/contract migrations. Восстановление данных после открытия writes — отдельная подтверждаемая recovery-операция в новую PostgreSQL database, а не часть автоматического deploy rollback.

## 12. Update, backup и restore

### 12.1 Immutable release protocol

- release manifest содержит Git SHA, image digests, checksums, schema revision и CI provenance;
- архив распаковывается в уникальную staging/release directory, а не поверх live checkout;
- `host.env`, `tuna.env` и другие host-owned secrets никогда не входят в release и не удаляются cleanup-ом;
- внешний ops-agent/supervisor выполняет switch и restart;
- после запуска проверяются `/health/ready` и domain smoke tests;
- автоматический rollback переключает только последний code release, совместимый с текущей schema; он никогда не откатывает DB state после открытия writes;
- устаревшие файлы исчезают за счёт immutable directory/image, а не опасного `find` в live tree.

Это устраняет существующий `cp` fallback, способный удалить env-файлы, и отсутствие отката кода.

### 12.2 PostgreSQL backup contract

Backup состоит из:

- PostgreSQL custom-format `pg_dump`;
- archive attachment/data tree;
- manifest с версиями, schema revision, counts и checksums.

Redis исключён. Секретные значения environment также исключены; manifest содержит только необходимые несекретные метаданные.

Host-managed secrets имеют отдельный recovery contract вне application backup: владелец хоста сохраняет `host.env`/secret store в защищённом контуре с доступом только оператору. Application manifest записывает только versioned fingerprint `HMAC-SHA256(SECRET_KEY, "robopark-secret-key-fingerprint-v1")`, но не сам ключ. Restore preflight вычисляет fingerprint доступного на целевом хосте `SECRET_KEY` и отказывает до `pg_restore`, если он не совпадает. Плановая ротация ключа является отдельной операцией с транзакционным re-encryption credentials и новым fingerprint.

Restore выполняется ops-agent-ом в новую PostgreSQL database при quiesced writers. После `pg_restore` выполняются validation и smoke tests, затем controlled database switch. Destructive `pg_restore --clean` из API-процесса запрещён.

Плановый backup должен иметь retention и off-host copy. До объявления backup готовым один архив восстанавливается на чистом стенде.

## 13. Quality gates и CI

В репозитории появляется единая команда `verify`, которую локально и в CI выполняют одинаково. Она включает:

- Ruff check/format и backend tests;
- frontend lint, build и tests;
- permission/navigation tests;
- deterministic blocker-history tests с injected `Clock`/явным `now`;
- Alembic upgrade на чистых SQLite и PostgreSQL;
- schema parity;
- Compose configuration и production image build;
- PostgreSQL + Redis integration tests для grants, throttle, locks, jobs и migration importer.

Python lockfile хранится в Git и устанавливается frozen-режимом. Production image не устанавливает pytest/dev dependencies. Base images и CI actions фиксируются immutable digest/SHA.

Критичные security/integration scenarios:

- snapshot/section без resolve запрещены;
- grant одной сессии не работает в другой;
- десятки poll не обращаются к Tracker;
- новый resolve после отзыва ticket запрещён, старый grant живёт не дольше TTL;
- Redis outage не превращает auth/Emergency в fail-open;
- конкурентные login failures между workers блокируются атомарно;
- упавший single-flight leader и job owner освобождаются TTL;
- вход A → logout → вход B не показывает данные A;
- downgrade permissions и смена парка не отображают last-good payload;
- migration parity и restore проходят на копии production snapshot.

## 14. Дополнительное прикладное hardening

Известные дефекты, не требующие отдельной архитектуры, включаются в соответствующие небольшие commits:

- Nginx body limit согласуется с разрешёнными API вложениями в 15 MiB; превышение получает управляемый `413`;
- archive/upload обрабатывается потоково с лимитами compressed size, uncompressed size, числа файлов, глубины путей и compression ratio;
- report body/comment и другие крупные text inputs получают явные server-side ограничения и одинаковую web-валидацию;
- при создании/одобрении пользователя проверяются role/park invariants: mechanic имеет ровно один парк, одобренный operator — хотя бы один;
- containers запускаются без root там, где это уже совместимо, с `cap_drop`, ограниченными writable mounts и log rotation;
- production dependency scan и SBOM становятся release-артефактами;
- polling приостанавливается для скрытой вкладки и не запускается на login/non-app routes.

Эти изменения не смешиваются с PostgreSQL cutover. Для каждого лимита добавляются boundary tests, а существующие допустимые пользовательские сценарии остаются совместимыми.

## 15. Этапы поставки

### Этап 0 — доверие к baseline

- немедленно закрыть direct Emergency ACL bypass временной fail-closed scope-проверкой;
- восстановить `0015` и attachment parity;
- исправить frontend build/lint, Ruff и time-dependent tests;
- добавить единый reproducible verify;
- исправить удаление env и обязательную readiness-проверку в текущем deploy path.

**Gate:** два последовательных запуска verify на чистом checkout зелёные; существующая SQLite открывается migration history.

### Этап 1 — security и browser isolation

- ввести private Redis и общий coordinator;
- закрыть Emergency bypass grants-моделью;
- перенести auth throttle;
- исправить frontend subject/scope cache, abort races и UI permissions.

**Gate:** security matrix и multi-worker Redis tests зелёные; ни один direct endpoint не обходит resolve.

### Этап 2 — platform seams и безопасная эксплуатация

- выделить ports/adapters вокруг DB, cache, jobs, upstream и snapshots;
- внедрить immutable release protocol;
- сделать backup/restore engine-aware;
- подготовить PostgreSQL migrations/importer и Compose integration environment.

**Gate:** failure/retry/rollback scenarios оставляют текущие данные и secrets неизменными, а active code — совместимым с текущей schema; смешанный live checkout невозможен.

### Этап 3 — PostgreSQL rehearsal и cutover

- провести импорт и restore drill на production snapshot copy;
- зафиксировать длительность и результаты parity;
- выполнить maintenance cutover и go/no-go;
- наблюдать PG-compatible release без destructive migrations.

**Gate:** PostgreSQL является единственным source of truth; pre-cutover artifacts проверенно восстанавливаются.

### Этап 4 — упрощение и hardening

- вывести file-based live merge из production path;
- завершить domain modularization затронутых областей;
- удалить временные compatibility adapters после observation window;
- включить регулярный off-host backup и restore drill.

**Gate:** нет второго coordination plane, legacy temporary path и SQLite runtime dependency.

## 16. Наблюдаемость и эксплуатационные сигналы

Структурированные logs и metrics должны показывать без секретов и персональных payload:

- PostgreSQL pool saturation, transaction errors и migration revision;
- Redis latency/errors, rejected writes, memory и active leases;
- Emergency grant issue/deny/expire counts;
- auth throttle decisions;
- cache hit/miss, single-flight wait и upstream latency;
- backup/import/restore job phase, duration и manifest ID;
- deploy release SHA, readiness и rollback reason.

Alerts требуются для PostgreSQL unavailable, Redis security-plane unavailable, failed backup, failed restore drill, repeated deployment rollback и approaching Redis memory limit.

## 17. Критерии завершения программы

Программа считается завершённой, когда одновременно выполнено следующее:

1. Все локальные и CI quality gates зелёные и воспроизводимы.
2. Emergency direct-read bypass и browser cross-subject cache исключены тестами.
3. Login throttle, shared cache, locks и job leases корректны между workers.
4. Production работает на PostgreSQL; SQLite не используется runtime-кодом.
5. Redis не содержит постоянных или авторизационно-значимых бизнес-данных.
6. Production migration имеет сохранённый validation report.
7. Update откатывает код к release, совместимому с текущей schema, не откатывая новые данные и не затрагивая host secrets.
8. PostgreSQL backup вместе с attachments восстановлен на чистом стенде.
9. Tuna topology и внешние пользовательские URL не изменились.
10. В production path отсутствуют file-based live-merge locks/blobs.

## 18. Реализационная декомпозиция

Это umbrella-spec с общими архитектурными ограничениями, а не единый implementation plan. Первый план после утверждения документа охватывает только Этап 0 и разбивает его на небольшие commits/checkpoints. Любой checkpoint оставляет repository тестируемым, а deployable checkpoints — откатываемыми.

После зелёного Этапа 0 следующие независимые подсистемы получают собственные design specs, пользовательское утверждение и implementation plans:

1. Emergency grants + Redis security coordination + auth throttle;
2. browser subject isolation, `authz_version` и UI permission fixes;
3. immutable deploy + engine-neutral backup/restore;
4. PostgreSQL portability, importer, rehearsal и production cutover;
5. Redis live cache/single-flight/job leases и удаление file coordination;
6. оставшееся application hardening и направленная domain modularization.

Эти specs обязаны соблюдать решения umbrella-spec и могут уточнять внутренние интерфейсы, но не менять source-of-truth, fail-closed security rules или rollback semantics без нового явного согласования. PostgreSQL production cutover не включается в тот же commit/релиз, что Redis security plane или frontend cache rewrite.
