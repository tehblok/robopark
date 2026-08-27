# Локальные тестовые аккаунты

Включите в `apps/api/.env`:

```env
DEV_SEED=true
```

Быстрый запуск демо-стенда одним скриптом (API + web, реальные Startrek/Emergency, без Tuna):

```bash
scripts/dev-demo.sh          # start
scripts/dev-demo.sh status   # проверить, что оба слушают
scripts/dev-demo.sh stop     # аккуратно погасить
```

Скрипт очищает `HTTP(S)_PROXY`, чтобы фоновые задачи (`blocker_history_job`, `emergency_keepalive`) не упирались в чужой прокси и не спамили 403 в логах.

После перезапуска API (`uvicorn …`) создаются пользователи и парк **Demo** (только если их ещё нет в БД). Пароли не перезаписываются — при смене пароля вручную seed не трогает существующую запись.

| Логин | Пароль | Роль | Кабинет |
|---|---|---|---|
| `royal` | `RoboparkRoyal!1` | royal | `/dashboard` |
| `admin` | `RoboparkAdmin!1` | admin | `/dashboard`, `/admin` |
| `operator` | `RoboparkOperator!1` | operator (approved) | `/dashboard`, парк Demo |
| `mechanic` | `RoboparkMechanic!1` | mechanic | `/dashboard`, парк Demo, Startrek `mechanic.dev` |
| `operator_pending` | `RoboparkPending!1` | operator (pending) | `/operator/pending` |
| `operator_rejected` | `RoboparkRejected!1` | operator (rejected) | `/operator/rejected` |

Парк **Demo** (`tag=Demo`, очередь `ROBOPARK`) создаётся автоматически; operator и mechanic получают к нему доступ.

**Только для локальной разработки.** В `deploy/host.env` держите `DEV_SEED=false` (или не задавайте).
