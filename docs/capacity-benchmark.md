# Изолированная проверка API на 200 сессий

`scripts/capacity_benchmark.py` запускает два production Uvicorn worker и одноразовый PostgreSQL 17 в Docker. Создаются 200 пользователей, 200 независимых cookie-сессий, 200 репортов и два парка. Реальными остаются auth, RBAC, park scope, SQLAlchemy pool, кеши, сериализация, production-приложение и локальные записи. Только Tracker/Emergency заменены loopback stub; соединения worker за пределы loopback запрещены.

Проверка выполняет холодные и тёплые list/detail/robot фазы, реалистичный cadence и bounded stress. Все 200 сессий участвуют в каждой stress-серии, одновременно выполняется не больше 20 запросов: это согласовано с ограниченным DB/thread pool и не выдаётся за 200 одновременных SQL checkout. Для каждого запроса детерминированно моделируются Wi‑Fi delay и loss с одной повторной попыткой.

Отчёт содержит p50/p95/p99, RPS, HTTP/request bytes, upstream-вызовы, cache hit/miss, DB pool checkout, CPU, RSS, диск PostgreSQL, статусы и проверки целостности. Gate не придумывает универсальный latency SLO для неизвестного ARM64-хоста: он сохраняет измеренные пределы и требует два worker, PostgreSQL 17, ровно 200 раздельных сессий, отсутствие неожиданных ответов/5xx/timeout, утечки чужих данных, дублей mutation и потери локальных записей.

## Повторить

Нужен работающий Docker и зависимости API:

```sh
apps/api/.venv/bin/python scripts/capacity_benchmark.py \
  --users 200 --duration 60 --cadence 3 \
  --wifi-delay-ms 35 --wifi-loss-percent 1 \
  --max-in-flight 20 --presence \
  --output /tmp/robopark-capacity.json
```

Сценарий не принимает URL существующего сервера или существующую БД, не читает `.env`, удаляет контейнер/volume после прогона и создаёт output без секретов. Внешняя сеть, Tuna/TLS, реальный Tracker/Emergency и производительность целевого Armbian остаются отдельным deployment gate.
