# Проверки аудита Codex

Дата: 2026-09-19

## Baseline

| Проверка | До исправлений |
| --- | --- |
| API canonical | FAIL: 19 файлов не прошли format gate |
| API pytest напрямую | `1740 passed, 1 skipped` |
| Web canonical | PASS: `2032` tests |
| Host | FAIL: `682 passed, 2 failed` из-за версии `0.1.40/0.1.42` |
| Docker | PASS |

## TDD и регрессии

| Область | RED-доказательство | GREEN |
| --- | --- | --- |
| Ops upload memory | disk-backed upload превышал memory threshold; первый фикс всё ещё резервировал лимит | memory regression + `66` ops/restore/bridge tests |
| Version | 2 host release tests падали | те же тесты проходят |
| Tracker capabilities | 4 ложных capability | `46` tracker-read tests |
| Shared issue/SLA | follower терял SDK-backed SLA | shared/follower suites проходят |
| Invalidation races | stale leader и namespace clear могли конфликтовать | exact/prefix/clear concurrency tests проходят |
| Tracker history | `192` upstream history, `11` timeout | один shared load на issue, 0 timeout |
| Park offline | fleet failure давал пустой park scope | scope сохраняется, warning/retry видимы |
| Emergency view | маршрут отсутствовал (`404`) | один scope-check и payload fetch; legacy calls не нужны |
| Benchmark help | `--help` завершался traceback | CLI regression test проходит |

## Финальная нагрузка — 200 сессий

Evidence: `/private/tmp/robopark-codex-capacity-2026-09-19-final-v3.json`.
Использованы временная SQLite, 200 разных users/sessions, два Uvicorn worker и
loopback Tracker/Emergency stub. Реальные сервисы не затрагивались.

| Метрика | Результат |
| --- | ---: |
| Сессии / workers | 200 / 2 |
| Cadence | 2000 запросов за 29.997 s |
| Throughput | 66.67 RPS |
| P50 / P95 / P99 | 8.05 / 113.33 / 229.11 ms |
| Неожиданные / 5xx / timeout | 0 / 0 / 0 |
| Stress | 600 запросов, 112.67 RPS, p95 1722.02 ms |
| Peak RSS, сумма workers | 363.09 MiB |
| SQLite | WAL, integrity `ok`, 200 users / 200 sessions |
| Cold search / issue detail | 1 / 1 upstream call |
| Cold combined Emergency view | 1 robot search + 1 payload call |
| Acceptance criteria | все `true` |

Отдельный реалистичный RED-прогон до history-фикса зафиксировал `192` history
calls и `11` timeout. Финальный прогон подтверждает cross-worker coalescing.

## Финальные canonical gates

| Команда/этап | Результат |
| --- | --- |
| `./scripts/verify.sh api` | PASS: ruff; `1750 passed, 1 skipped`, 21 warning |
| `./scripts/verify.sh web` | PASS: lint; build; `141` files / `2034` tests; 30 routes |
| `./scripts/verify.sh docker` | PASS: Compose, API image, web image, no pytest in runtime API |
| `./scripts/verify.sh host` | PASS: `685 passed`; installer `52 passed` |
| Signed OTA | PASS: pack + detached Ed25519 signature/checksum/metadata/content verify |

Web lint завершился кодом 0, но оставил известные React warnings из
`REMAINING.md`. API warnings — Starlette `TestClient` и sqlite datetime adapter.
