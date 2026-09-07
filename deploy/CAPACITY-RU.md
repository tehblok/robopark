# Приёмка 200 одновременных пользователей

Это воспроизводимый **gate на целевом хосте**, а не заявление о производительности Mac или непроверенного Armbian. До выполнения checklist статус: **НЕ ВЫПОЛНЕНО**. Используется `scripts/capacity-gate.py`, Python 3.12+ и `httpx` из `apps/api/uv.lock`; дополнительные нагрузочные сервисы не нужны.

## Профили и ограничения

Общий production renderer применяется и installer, и OTA. Профиль выбирается через `UVICORN_WORKERS` в защищённом answer-файле до установки; installer разрешает 4 workers только при RAM ≥24 GiB. Ручной Compose-путь сохраняет свою конфигурацию и не получает эти ограничения автоматически.

| Настройка | Armbian 8 GiB | Ubuntu/Armbian 32 GiB |
|---|---|---|
| `UVICORN_WORKERS` | 2 | 4 |
| API container `mem_limit` | 3 GiB | 12 GiB |
| Web container `mem_limit` | 256 MiB | 256 MiB |
| `pids_limit` каждого контейнера | 512 | 512 |
| Docker `ulimits.nofile`, soft/hard | 65536/65536 | 65536/65536 |
| Host systemd `LimitNOFILE` | 65536 | 65536 |
| Host systemd `TasksMax` | 4096 | 4096 |
| SQLite | WAL, busy_timeout 5000 ms | WAL, busy_timeout 5000 ms |
| Начальный workload | 200 клиентов, 1 s think time | Такой же, для сравнения |

Лимит systemd ограничивает host utility, Docker CLI и Tuna; контейнеры получают отдельные Docker ulimits/PID/memory limits. Эти значения оставляют часть RAM для ОС, page cache и сборки кандидата. Docker BuildKit находится вне memory limit API; на 8 GiB выполняйте только одну сборку/OTA за раз, следите за памятью и OOM. Swap не заменяет достаточную RAM. Смена workers в уже работающем env требует согласованной генерации нового runtime при следующем OTA; не редактируйте immutable `current-compose.json` вручную.

Проверьте фактические лимиты без вывода env/secrets:

```sh
sudo systemctl show robopark.service robopark-tuna.service -p LimitNOFILE -p TasksMax
sudo docker compose --project-name robopark --file /var/lib/robopark/ops/state/current-compose.json ps
sudo docker inspect --format '{{.HostConfig.Memory}} {{.HostConfig.PidsLimit}} {{json .HostConfig.Ulimits}}' "$(sudo docker compose --project-name robopark --file /var/lib/robopark/ops/state/current-compose.json ps -q api)"
```

Не используйте полный `docker inspect`, `env`, `ps e` или Compose config с разрешением env для отчёта: они могут раскрыть конфигурацию.

## Что моделирует стенд

200 coroutine-клиентов стартуют вместе, каждый выполняет один запрос за раз, после него ждёт 1 секунду. Warmup — 30 секунд, измерение — 600 секунд; throughput считается по всему измерению, включая ожидание последних ответов. HTTP keep-alive включён, максимум соединений равен числу клиентов. Timeout — 10 секунд, redirects отключены, HTTPS проверяет сертификат; proxy-env не используется. HTTP без TLS допускается только для loopback. Стенд лучше запускать с отдельной машины в той же сети; отдельный обязательный прогон выполните через стабильный Tuna HTTPS.

Safe GET mix равномерно проходит `/api/auth/me`, `/api/operator/parks`, `/api/reports/mine`, `/api/reports/badge`. Они читают приложение/SQLite и не запускают Tracker/Emergency actions. Вход в систему выполняется заранее: password/login brute-force limiter не входит в измерение. Все клиенты используют одну тестовую сессию — это проверка 200 параллельных клиентов, а не 200 различных учётных записей. Для проверки персонализированных объёмов/прав повторите прогоны с типичными отдельными тестовыми учётными записями и реалистичным набором данных.

Опциональный write mix делает 10% PATCH-запросов **только к новому уникальному тестовому парку**, остальные запросы — safe GET. Перед прогоном создаётся парк с `capacity-<uuid>` в имени/tag, он сразу отключается; после прогона `finally` снова выставляет `is_active=false`. Существующие парки не изменяются. Удаления парка нет в API: отключённая запись сохраняется для аудита и ручной уборки по правилам команды. ID новой записи попадает в агрегированный отчёт. Запись разрешена только при одновременных `writes=true`, `isolated_test_data=true` в private config и **`ALLOW_ISOLATED_WRITES=true`** в окружении. Используйте отдельный тестовый контур с Royal-тестовой сессией; отказ cleanup блокирует PASS.

## Секретный конфиг

Создайте config через editor, mode строго 0600; владелец — текущий пользователь или root. Symlink, неизвестные/повторяющиеся ключи, URL с userinfo/query/fragment, небезопасный session token и неверные диапазоны отклоняются. URL и cookie не передаются через CLI/process list и не включаются в JSON-отчёт.

```sh
umask 077
install -m 0600 /dev/null capacity-private.json
${EDITOR:-vi} capacity-private.json
```

В файле задайте JSON-поля:

| Поле | Значение |
|---|---|
| `base_url` | Точный публичный HTTPS origin; без `/api`, query и credentials |
| `session` | Значение cookie `robopark_session` тестового аккаунта, полученное через защищённый browser/session workflow |
| `users` | 200 (default) |
| `duration_seconds` | 600 (default) |
| `warmup_seconds` | 30 (default) |
| `think_seconds` | 1 (default) |
| `writes` | false (default); true только на изолированном контуре |
| `isolated_test_data` | true только для разрешённого write-прогона |

Не копируйте config в Git, чат, screenshots или диагностический ZIP. Не запускайте стенд с `set -x` и HTTP debug logging. Путь config можно передавать в CLI; его содержимое — нельзя.

## Запуск и evidence

В проверенном checkout установите project dependencies (`uv sync --project apps/api --frozen --extra dev`). На устройстве запись server logs организует оператор с root; логи могут содержать чувствительные строки, храните их с mode 0600. Отметьте время начала и окончания прогона. Сохраните только этот интервал API/systemd/Docker/kernel logs. Запишите до/после `RestartCount` и `State.OOMKilled` контейнеров; при увеличении restart count добавьте в evidence строку `restart_count_increased`, при OOM — `oomkilled`. Не передавайте полные inspect/env данные.

```sh
uv run --project apps/api --frozen --extra dev python scripts/capacity-gate.py \
  --config capacity-private.json --output capacity-read.json
```

Для отдельного разрешённого write-прогона:

```sh
ALLOW_ISOLATED_WRITES=true uv run --project apps/api --frozen --extra dev \
  python scripts/capacity-gate.py --config capacity-private.json --output capacity-write.json
```

Первые результаты обычно имеют `PENDING_SERVER_EVIDENCE` и exit code **2**, даже при хороших latency/rps: без журналов сервера gate закрыт. В отчёте имеются throughput, p50/p95/p99 (nearest-rank, миллисекунды), error rate (доля, не проценты), число запросов, продолжительность, счётчики наблюдаемых отказов, ID тестового парка и результат cleanup. Ответы, заголовки и тексты exception не сохраняются. Любой non-2xx, timeout, transport error или слишком большой ответ считается ошибкой.

После получения защищённого server-log за полный интервал оцените **новый** отчёт:

```sh
uv run --project apps/api --frozen --extra dev python scripts/capacity-gate.py \
  --evaluate capacity-read.json --server-log target-run.log --output capacity-read-final.json
```

Log scanner считает строки с SQLite lock/busy, event-loop exceptions/slow callbacks, OOM и restart markers; наружу выходят только числа. Это наблюдение ошибок по журналам, а не измерение внутренней event-loop latency. Отсутствие строк в пустом/неполном log не является доказательством: оператор обязан подтвердить полный интервал, enabled logging, отсутствие OOM/restarts через inspect/kernel logs и приложить время/идентификатор прогона. Raw logs и cookie не прикладываются к общедоступному отчёту.

## Точные критерии

Для каждого обязательного прогона (локальный/через Tuna; чтение и разрешённая запись на тестовом контуре) должны одновременно выполняться:

- ровно **200** клиентов; warmup ≥30 s, измерение ≥600 s;
- throughput **≥100 запросов/секунду**;
- p50 **≤250 ms**, p95 **≤1000 ms**, p99 **≤2000 ms**;
- error rate **≤0.001** (0.1%); все auth/RBAC ошибки тоже входят в него;
- **0** DB lock, event-loop failure, OOM и непреднамеренных container restart;
- нет переполнения лимита samples; `cleanup_ok=true` для write-прогона;
- readiness/HTTPS/Royal login исправны после нагрузки, host doctor не имеет failed checks; свободные дисковые места/inodes и thermal/memory остаются в рабочих пределах.

`PASS`/exit 0 выдаётся только при выполнении числовых условий и нулевых server failure counts. `FAIL` или незавершённый evidence → exit 2; ошибка конфигурации/запуска → exit 1. Файлы output создаются без перезаписи существующего пути. Автоматический PASS всё равно сопровождается ручными ресурсными и функциональными проверками из [INSTALL-ARMBIAN-RU.md](INSTALL-ARMBIAN-RU.md).

| Запись целевого прогона | Результат |
|---|---|
| Хост, архитектура, OS, RAM/CPU, профиль | **НЕ ВЫПОЛНЕНО** |
| Dataset, тестовая роль, версия/commit, run_id, время | **НЕ ВЫПОЛНЕНО** |
| 200-client read через локальный маршрут | **НЕ ВЫПОЛНЕНО** |
| 200-client read через Tuna HTTPS | **НЕ ВЫПОЛНЕНО** |
| Opt-in isolated write + cleanup | **НЕ ВЫПОЛНЕНО** |
| Server logs + restart/OOM counters + doctor | **НЕ ВЫПОЛНЕНО** |
| p50/p95/p99, rps, error rate, заключение | **НЕ ВЫПОЛНЕНО** |
