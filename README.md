# Robopark

## Установка и обновление

Текущие исходники готовят помощник под обученную Gemma 4 E4B на AGX Orin
без встроенного корпуса знаний. До получения модели генерация выключена,
базовые веса автоматически не скачиваются. Профиль параллельной работы,
подключение GGUF и состояние варианта TensorRT: [локальный ИИ](docs/runbooks/local-ai.md).
В rc.36 Telegram стал встроенным сервисом с общей БД парков и правами доступа.
Отчёты используют оформление отдельного бота; поддерживаются расписания, Zoom,
одобрение доступа, поиск и история роботов. [Текущий выпуск rc.39](docs/releases/0.2.0-rc.39.md).

На новом Ubuntu/Armbian-хосте с Python 3.10+, `curl` и `sudo` одна команда
скачивает rc.39, сверяет SHA-256 и запускает преднастроенную установку владельца:

```sh
d=$(mktemp -d) && curl -fL --retry 3 --connect-timeout 20 --proto '=https' --tlsv1.2 'https://github.com/tehblok/robopark/releases/download/v0.2.0-rc.39/robopark-0.2.0-rc.39.ota' -o "$d/robopark.ota" && (cd "$d" && printf '%s\n' '60a0e8a3931ee90e32fc871b37e2020f538de9b2cd0a3809216ccca06c4bb405  robopark.ota' | sha256sum -c - && sudo python3 robopark.ota install --preset robopark)
```

Профиль задаёт `robopark.ru.tuna.am`, владельца `tehblokdan` и SSH через `ssh`.
Потребуются отдельный ключ `robopark-install-preset.key`, Tuna token и новый
пароль владельца. Восстановятся 11 парков, 11 чатов и 74 задания из сохранённой
конфигурации бота. Задания выключены до проверки адресатов и расписаний.
Прежних пользователей, cookies и полной конфигурации выключенного хоста
в этом наборе нет: [что входит в профиль](docs/runbooks/owner-install-preset.md).

Для обычного меню запускайте скачанный пакет без `install --preset robopark`.
При найденных данных Robopark чистая установка откажет; для работающего хоста
используйте OTA-обновление. Поддерживаемые исходные версии указаны в manifest.
Требования к ОС, памяти и дискам: [установка](docs/runbooks/usb-clean-install.md).
Проверки и их границы: [отчёт rc.36](docs/verification/2026-10-07-native-bot-parity.md).

Чистая установка rc.36 автоматически включает SSH для владельца через
алиас Tuna `ssh` с прежним публичным ключом:
[установка, ключ и переезд на AGX](docs/runbooks/support-ssh.md).
Преднастроенный профиль восстанавливает доступную конфигурацию старого бота:
[состав, запуск и отдельный ключ](docs/runbooks/owner-install-preset.md).
В Git находятся публичный SSH-ключ и зашифрованные архивы; закрытые значения
в установочную команду не входят.

> **GEACX1 и NVMe:** rc.25 поддерживает NVMe с ОС и eMMC с ОС + NVMe для данных.
> Порядок выбора и проверки описан в
> [инструкции по хранилищу GEACX1](docs/runbooks/geacx1-storage.md).
> ИИ устанавливается только на подтверждённый AGX Orin. Физическая проверка
> GEACX1/JetPack 7.2 пока не выполнена; скорость и качество ответов не измерены.
> Диагностика AGX, кэширование и короткие проверки описаны в
> [руководстве по производительности](docs/runbooks/geacx1-performance.md).

Каноническая поставка — один файл `robopark-<версия>.ota`. Сборка:

```sh
./scripts/build-ota.sh /absolute/output/directory
```

USB: `sudo python3 robopark-<версия>.ota`. Royal обновляет систему на странице
«Система → Обновление» с resumable-загрузкой и автоматическим rollback.
См. [runbooks](docs/runbooks/build-ota.md). SHA-256 обеспечивает целостность,
но не аутентифицирует издателя.

Версия исходников задаётся в [VERSION](VERSION). Production-профиль и чистый
установщик используют **PostgreSQL 17**. Для Armbian/Ubuntu смотрите
[руководство оператора](deploy/INSTALL-ARMBIAN-RU.md) и
[приёмку 200 пользователей](deploy/CAPACITY-RU.md). Результаты подготовки
версии 0.2.0 и оставшиеся проверки приведены в
[релизном отчёте](docs/reviews/2026-10-01-production-readiness.md).
Готовность к выпуску определяется проверками конкретного пакета; старые
release-evidence не подтверждают готовность изменённых исходников.

Web-first fleet operations system (admin / operator / mechanic).

- **Primary:** website on local host (API + app); remote access via **Tuna HTTPS tunnel** (no VPS, mechanics open a link).
- **Telegram:** встроенный сервис с парками, правами и расписаниями Robopark;
  [настройка и эксплуатация](docs/runbooks/telegram.md),
  [устройство сервиса](docs/architecture/2026-10-07-native-telegram-service.md).

## Phase 1

Исторический skeleton-этап: FastAPI + React monorepo, session auth, role
cabinets и Docker Compose на хосте. Текущий production runtime уже не
использует SQLite: база работает на PostgreSQL 17, публичный доступ —
через [Tuna](https://tuna.am/docs/).

## Phase 2

Operator onboarding and parks: shared-password registration, admin/royal access
approval with park assignment, minimal park CRUD, and operator park-request
inbox.

Set `OPERATOR_SHARED_PASSWORD` in `.env` (local) or `host.env` (deploy). When
unset or empty, `POST /auth/register` returns 403 and the register page is
closed. Never commit a real value.

### Registration and access

1. Operator opens `/register`, enters the shared password plus username and
   password, then signs in at `/login`.
2. New operators start as `access_status=pending` and land on `/operator/pending`.
3. Admin or royal creates parks in `/admin`, then approves the access request
   with at least one active park — the operator moves to `/dashboard`.
4. Reject sets `access_status=rejected` and routes to `/operator/rejected`; there
   is no self-serve re-apply.

### Parks and park requests

- Admin/royal manage parks (name, unique tag, active flag) from `/admin`.
- Approved operators see assigned parks and may request additional parks; admin
  approves or rejects those requests in the same inbox.

## Phase 3

Mechanic flows: admin-created mechanic accounts (exactly one park, immediately
approved), integration settings for Tracker token and Emergency cookie, extended
park Tracker fields, and mechanic tools for own-park tasks, cross-park robot
search, and Emergency VIN checks.

Tracker token and Emergency cookie are stored in the database and configured from
`/admin` — they are not environment variables. After creating a park with
`tracker_queue` and a mechanic assigned to it, the shared shell exposes
`/tasks`, `/robots/search`, and `/emergency` (legacy `/mechanic/*` URLs redirect).

## Phase 4

Operator tools: approved operators use the hub at `/dashboard` for read-only
Tracker workflows — blockers by assigned park (`/tasks`), cross-park robot search
(`/robots/search`), and the «Сейчас по Tracker» live metrics snapshot
(`/analytics`). Park assignment and park requests remain at `/operator/parks`.

These tools require a platform Tracker OAuth token in `/admin` and, per park,
`tracker_queue` plus feature flags: `feature_blockers` for the blockers list and
`feature_reports` for the now-report. Parks missing queue or flags are skipped
in the report with an inline reason.

## Phase 5

Tracker Core + Actions: unified `/tracker/*` API with role-aware ACL for
`admin`/`operator`/`mechanic`, issue read endpoints, and write actions
(comment/assign/unassign/transition/close).

**Scope is fail-closed.** For every issue an operator or mechanic touches, the
backend requires proof that the issue belongs to one of their parks:

1. the issue queue must be present and among the user's park queues;
2. the issue must carry the tag of one of the user's parks.

An issue tagged with a *different* park is always denied. An issue with no park
tag is reachable only while the `operator_show_untagged` policy is enabled, and
never for mechanics. Anything unverifiable — missing queue, no park assigned —
is denied rather than allowed. List endpoints silently filter out-of-scope
issues; single-issue endpoints return `403 tracker_issue_out_of_scope`.

`GET /tracker/issues` is paginated (`limit`, `offset`, default 50, max 200) and
returns `total` / `has_more` instead of silently truncating the result.

New endpoints:

- `GET /tracker/issues`
- `GET /tracker/issues/{key}`
- `GET /tracker/issues/{key}/comments`
- `GET /tracker/transitions/{key}`
- `POST /tracker/issues/{key}/comment`
- `POST /tracker/issues/{key}/assign`
- `POST /tracker/issues/{key}/unassign`
- `POST /tracker/issues/{key}/transition`
- `POST /tracker/issues/{key}/close`

Admin policy toggles are available at:

- `GET /admin/settings/tracker-policy`
- `PUT /admin/settings/tracker-policy`

## Phase 6

Emergency is now a shared, role-aware workflow for mechanics, operators,
admins, and royal users. Mechanics see the operational sections, operators
additionally see `position_route` and `metadata`, and only admins/royal users
see `service_raw`. The legacy `/mechanic/emergency/*` routes remain available
for compatibility.

Emergency section, field, order, enabled-state, and role configuration lives in
the database. Migration `0004_phase6_emergency_config` creates the tables and
seeds them from `apps/api/data/emergency_sections.json`; after migration, use
the admin UI at `/admin/emergency/config` to create, edit, reorder, enable, or
export sections instead of editing the seed file.

Robot payloads are cached in-process for 5 seconds, with one in-flight upstream
request per VIN. The API also runs a keep-alive loop over the 20 most recently
used VINs (or the optional seed VIN when the ring is empty), records cookie
validity, and marks it invalid after an upstream 401. Emergency pages load on
user actions and do not poll from browser timers.

## UI shell и дашборд

После входа все роли попадают в общий **AppShell**: слева сайдбар «Робопарк
Сервис», сверху — выбор **Парка**, имя пользователя и меню.

**Тема:** в меню пользователя (правый верхний угол) переключатель «Светлая /
Тёмная тема». Выбор сохраняется в `localStorage` (`robopark-theme`); по
умолчанию — светлая.

**Парк:** дашборд и связанные экраны зависят от выбранного парка в шапке.
У механика парк зафиксирован; у оператора и админа — выпадающий список
назначенных парков.

**Дашборд** (`/dashboard`): KPI и график «пришли / ушли» блокеров за 7 дней.
Сводка берётся из Tracker (now-report); история графика — из локальной БД.
Фоновый job API сканирует Tracker **каждые 2 часа** и пишет бакеты per-park;
пока job не отработал, график может быть пустым. Нужны `tracker_queue`, `tag`
и OAuth-токен Tracker в `/admin`.

**Заглушки «Скоро»:** Карта, Обучение, Помощь — пункты меню видны, контент
появится позже.

## Ремонтные задачи

В разделе **Работа** механик берёт задачу и оформляет результат короткой формой:

1. **Взять в работу** — одна кнопка. Для пустого поля система сначала ставит
   служебную компоненту `ROBOT_UNSORTED`, затем переводит задачу в работу.
   Уже указанные реальные компоненты сохраняются.
2. **Что ремонтируем** — в итоговом отчёте выберите деталь по понятному названию.
   Служебная компонента заменится выбранной; поиск понимает привычные синонимы.
3. **Что случилось** — неисправности для выбранной детали показаны первыми;
   полный список также доступен, запоминать код не нужно.
4. **Что сделали** — подходящие действия показаны первыми; подтвердите реально
   выполненное действие, добавьте фото и при необходимости уточнение.
   Текст отчёта составляется из выбранных значений автоматически.
5. **Передать на проверку** — оператор проверяет результат и закрывает задачу
   либо возвращает её в работу.

Форма предлагает поля по названию и описанию задачи, комментариям текущего ремонта
и вашему уточнению. В «Подсказках по тексту» видны предлагаемые значения и источник.
Нажмите «Подставить поля»; если уже что-то выбрано, сначала показана замена.
Подстановку можно отменить, фото и текст сохранятся. История похожих ремонтов
помогает упорядочить действия; выполненную работу подтверждает механик.
Это локальные правила, работающие и на Khadas, без ИИ-модели.

При сбое отправки фото и уточнение остаются в форме или очереди синхронизации.
Конкурирующие изменения полей требуют проверки перед повторной отправкой.
Подробнее о совместимости и восстановлении: [repair workflow](docs/architecture/2026-10-04-repair-workflow.md).

## Репорты

Цепочка «механик → оператор → админ» в `/reports`. KPI Tracker (now-report)
остаётся на **Дашборде**, не смешивается с человеческими репортами.

### Виды (`kind`)

| kind | Кто создаёт | Получатель |
|------|-------------|------------|
| `ticket_question` | Механик (форма «Вопрос по тикету») | Оператор парка |
| `ticket_close_review` | Автоматически при «Закрыть» тикет в UI | Оператор парка |
| `mechanic_problem` | Механик (форма «Проблема») | Оператор парка |
| `escalation_to_admin` | Оператор (эскалация) | Админ / royal |

Для `ticket_question` и `ticket_close_review` обязателен ключ тикета Tracker;
для `mechanic_problem` — опционален.

### Закрытие тикета → оператор

Механик нажимает «Закрыть» в задачах Tracker. Бэкенд сначала выполняет
переход в Tracker; только после успешного закрытия создаётся репорт
`ticket_close_review` со статусом `open` для операторов этого парка. Повторное
открытое ревью по тому же `(park_id, tracker_key)` не дублируется.

### Действия получателя

- **Вернуть** — статус `returned`, обязателен комментарий; механик видит его в
  `/reports` и в списке задач (возвращённые close-review).
- **Готово** — статус `done`, репорт уходит из активного inbox (не удаляется).
- **Закрыть** — только закрыть карточку в UI, статус не меняется.
- **Эскалировать** (оператор) — новый репорт `escalation_to_admin` в inbox
  админа.

### Бейдж в меню

На пункте «Репорты» в сайдбаре:

- **Механик** — число своих репортов со статусом `returned`.
- **Оператор** — число `open` в inbox по назначенным паркам.
- **Админ / royal** — число `open` эскалаций.

Счётчик обновляется при навигации и при возврате на `/reports`.

## Local development

### API

Python 3.12 or newer is required. Create the environment, install the API, copy
the example configuration, run the database migration, and start Uvicorn:

```bash
cd apps/api
python3.12 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
cp ../../.env.example .env
mkdir -p data
alembic upgrade head
uvicorn robopark_api.main:app --reload --app-dir src
```

Before the first start, set `SEED_USERNAME`, `SEED_PASSWORD`, and `SEED_ROLE`
in `apps/api/.env`. The seed user is created only when it does not already
exist; use a strong password and remove `SEED_PASSWORD` from the environment
after the user has been created. To enable operator self-registration, also set
`OPERATOR_SHARED_PASSWORD` in `apps/api/.env`. The API is available at
`http://127.0.0.1:8000`; check it with
`curl http://127.0.0.1:8000/health`.

**Local demo accounts:** set `DEV_SEED=true` in `apps/api/.env` and restart the
API. Logins and passwords — [`docs/DEV-ACCOUNTS.md`](docs/DEV-ACCOUNTS.md)
(for example `royal` / `RoboparkRoyal!1`, `operator` / `RoboparkOperator!1`).

One-shot demo bootstrap (API + web, real Startrek/Emergency, no Tuna):

```bash
scripts/dev-demo.sh          # start
scripts/dev-demo.sh status   # verify listeners
scripts/dev-demo.sh stop     # tear down
```

### Web

With the API running, start the web app in another terminal:

```bash
cd apps/web
npm install
npm run dev
```

Vite serves the app on `http://localhost:5173` and proxies `/api/*` to the
local API, stripping the `/api` prefix.

## Host installation

The host runs the API, PostgreSQL 17 database, and web app:

```bash
git clone <repository-url> robopark
cd robopark/deploy
sudo install -o root -g root -m 0600 host.env.example host.env
sudoedit host.env
```

Set a strong `SEED_PASSWORD`, optional `OPERATOR_SHARED_PASSWORD`, and
`CORS_ORIGINS=https://<your-tuna-host>` (exact HTTPS URL from Tuna — see deploy docs).
Keep `host.env` root-owned with mode `0600`; the wrapper validates it, creates
external root-private PostgreSQL credentials and generates the non-secret snapshot
projection used by the API. Use the wrapper for every production Compose command:

```bash
sudo ./compose-production.sh up -d --build --wait
sudo ./compose-production.sh config --quiet
sudo ./compose-production.sh ps
sudo ./compose-production.sh logs --tail=100 api web
```

`host.env` is gitignored. For **Tuna HTTPS**, set `COOKIE_SECURE=true`. The API is
not exposed on port 8000 — use the web container on **localhost:8080**, then run
the Tuna agent (see [`deploy/README.md`](deploy/README.md)).

Full topology: [`deploy/README.md`](deploy/README.md).

## Remote access (Tuna)

There is **no VPS**. Install [tuna-cli](https://tuna.am/docs/guides/install/) on the
host, point it at `127.0.0.1:8080`, prefer a Russian `--location`, and give mechanics
the HTTPS link.

```bash
# after Docker is up — see deploy/tuna.service for production
export TUNA_TOKEN=tt_***
tuna http 127.0.0.1:8080 --subdomain=robopark --https-redirect
```

Put the printed `https://…` origin into `CORS_ORIGINS` and restart the API container.

## Security

### Secrets at rest

The Tracker OAuth token and the Emergency cookie are stored in the database and
encrypted with a key derived from `SECRET_KEY` (Fernet, AES-128-CBC + HMAC).
Without `SECRET_KEY` the API still starts, but refuses to persist Tracker and
Emergency secrets (`MissingSecretKeyError`) — set it in `.env` / `host.env`:

```bash
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

Values written before encryption keep working and are upgraded to ciphertext on
the next save. Changing or losing the key makes stored secrets unreadable: the
API reports the integration as «not configured» and the value must be re-entered
in `/admin`.

### Passwords and brute force

Registration and admin-created accounts must satisfy a password policy
(`PASSWORD_MIN_LENGTH`, default 12, plus three of four character classes).
`POST /auth/login` and `POST /auth/register` are rate limited per
username + address; after `LOGIN_MAX_ATTEMPTS` failures the pair is locked for
`LOGIN_LOCKOUT_SECONDS` and the API answers `429` with `Retry-After`.

Changing a mechanic's password or deactivating the account revokes their active
sessions immediately. Expired sessions are purged on login and by an hourly
background job.

### Audit trail

All Tracker writes go through one service token, so Tracker itself cannot tell
users apart. Every action is therefore recorded in `audit_log` — actor, role,
target issue, outcome (`success` / `failure` / `denied`), and address — and
comments written through the platform are signed with the real author.

- `GET /admin/audit` — filter by `action`, `actor_user_id`, `target_id`,
  `park_id`; paginated.
- `GET /admin/audit/actions` — known action names for UI filters.

Denied attempts are recorded too, so a blocked cross-park action leaves a trace.

### Snapshot and OTA update (royal only)

The owner tab **Администрирование → Снимок и обновление** can:

- download a full snapshot (database, files, `host.env`);
- restore that snapshot on this host or another (after one first Compose start and royal login);
- upload one `robopark-<версия>.ota`; browser upload is resumable and the host
  verifies SHA-256 again before snapshot/cutover.

Release flow: integrity check → tests on a copy → automatic snapshot → copy onto the checkout → `ops-agent` rebuilds `api` and `web`. Failed tests leave the live system unchanged. Other users see a maintenance screen for the whole job.

Admin cannot open this tab or call the APIs. Confirmation phrases: `ВОССТАНОВИТЬ` / `ОБНОВИТЬ`.

## Health and operations

- `GET /health` — liveness, dependency-free.
- `GET /health/ready` — readiness: verifies the database and reports integration
  state; returns `503` when the database is unreachable.

Compose services declare healthchecks, the API container runs as an
unprivileged user, and PostgreSQL 17 is the only production database. Its
credentials live in external root-private files and the database is not
published outside the host.

## Continuous integration

[`scripts/verify.sh`](scripts/verify.sh) is the single source of truth for local
and CI verification. The workflow runs its canonical no-argument form on every
push and pull request; this checks the frozen API environment, API lint and
tests, the frozen web install, web lint/build/tests/navigation, Compose config,
both Docker images, and the API runtime dependency boundary.

Locally, use the bounded gate while developing. The explicit `full` target is
the release gate and may start Docker and run for many minutes:

```bash
./scripts/verify.sh fast
./scripts/verify.sh api
./scripts/verify.sh web
./scripts/verify.sh full  # canonical release gate; requires Docker
./scripts/verify.sh load  # opt-in capacity benchmark
# soak is opt-in and also requires ROBOPARK_SOAK_DURATION_SECONDS/OUTPUT
```

Update API dependencies deliberately with `cd apps/api && uv lock && uv lock
--check`, then run the canonical full gate. Base-image digest updates are also
deliberate changes and require the full gate to pass before merge.

Remaining UI/UX backlog: [`docs/UI-REFACTOR-SPEC.md`](docs/UI-REFACTOR-SPEC.md).

## Scope boundaries

This repository is a clean implementation and has no runtime or build
dependency on the old bot repository.

Not included:

- Standalone copies of the legacy Telegram server or its host updater.
- VPS / WireGuard remote access (replaced by Tuna HTTPS tunnel).
- Importing an old production SQLite database; clean hosts start with PostgreSQL 17.
- Per-user Tracker credentials — the platform uses one service token and
  attributes actions through `audit_log` and comment signatures.

## Локальный помощник на AGX Orin

Начиная с rc.25 установщик поддерживает локальный Bonsai, встроенную базу знаний,
чат с источниками и управляемые интеграции/скрипты. Загрузка и запуск модели
разрешены только на подтверждённом AGX Orin с NVMe и CUDA; Khadas работает без неё.
Частные инструкции и выгрузки не входят в публичный репозиторий.
[Установка, импорт знаний и работа с автоматизациями](docs/runbooks/local-ai.md).
