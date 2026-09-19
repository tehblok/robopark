# Оставшиеся риски и roadmap

## Обязательная приёмка на Wi-Fi/ARM/Tuna

Локальный PASS не заменяет целевую проверку. Перед обещанием 200+ реальных
пользователей выполнить минимум 10 минут устойчивой нагрузки и отдельный
reconnect burst на тестовом ARM-сервере через тот же Wi-Fi и Tuna:

- 200 отдельных сессий, 60–70 RPS steady state и холодный reconnect burst;
- client p50/p95/p99, ingress/egress, packet loss/retries и Tuna throttling;
- CPU, load average, RSS каждого worker, OOM/restarts, temperature/throttling;
- SQLite lock evidence, disk latency/free space и полные server logs;
- реальные Tracker/Emergency latency/quota; затем snapshot → update →
  readiness → rollback/restore с данными и вложениями сопоставимого объёма.

## Следующий технический приоритет

1. Сделать lifecycle-owned pooled Emergency HTTP client вместо нового
   соединения на запрос; добавить host-wide slots, circuit breaker и stale
   fallback. Это особенно важно при потерях Wi-Fi.
2. Разделить capacity profiles: steady 60–70 RPS, массовый reconnect и
   degraded-upstream. Учитывать внешний лимит Tuna около 100 RPS как отдельный
   эксплуатационный бюджет, а не как предел приложения.
3. После profiling вынести presence pruning из heartbeat и проверить
   composite index; не менять схему без доказательства query plan.
4. Добавить watchdog внешнего пути (DNS/Tuna/upstream), но только после
   согласования порогов и политики рестартов, чтобы не создать restart storm.
5. Рассмотреть объединённый Tracker workbench endpoint по образцу Emergency
   view, если LAN/Tuna traces подтвердят лишние round trips.

## UX roadmap

1. Единый компонент offline/stale/retry для всех экранов, с временем
   последнего обновления и без ложного состояния «данных нет».
2. Debounce + cancellation поиска роботов, request jitter для массового
   открытия смены и prefetch только по намерению пользователя.
3. Operations pulse: связь сервера, Tracker, Emergency, backlog outbox,
   freshness и понятное действие для оператора.
4. Закрепляемые быстрые действия и адаптивные shortcuts в мобильной навигации.
5. MVP карты/местоположения роботов вместо заглушки, встроенные инструкции и
   контекстная помощь для редких операций.
6. Центр уведомлений по SLA/диагностике/возвратам и offline drafts для форм,
   если продуктовый сценарий допускает отложенную отправку.
7. Аудит ключей `ResourceStore` на обязательный user/park scope перед
   расширением persistent cache.

## Осознанно оставлено

- GET Tracker detail синхронно reconciles внешнее закрытие и пишет локально.
- Inventory idempotency key глобален, а не scoped по парку.
- React lint warnings: refs during render, dependency arrays, impure
  `Date.now()`, mixed fast-refresh exports.
- Starlette `TestClient` и sqlite datetime adapter deprecated на Python 3.13.
- Крупные backend-модули не дробились без функциональной необходимости.

## Граница доказанного результата

Benchmark подтверждает 200 session records, два API worker, RBAC/cache/SQLite
и loopback integrations. Он не эмулирует 200 браузеров, TLS/Tuna, слабый ARM,
реальный radio contention или часовое накопление памяти.
