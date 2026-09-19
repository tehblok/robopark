# Merge notes Codex audit

## Ревизии

- Исходный commit: `326964e96137cc29344bded9d60b67a7664b6022`.
- Последний проверенный code/test commit: `55372eec702b5a089b03ad55db44a979b7db353f`.
- Итоговый SHA ветки определяется после documentation commit через
  `git rev-parse HEAD`.

## Коммиты

1. `30bebf4` — `fix: bound ops upload memory`
2. `71bc4a7` — `fix: align ops runtime version with release`
3. `44cf661` — `style: restore API format gate`
4. `3ed3dd0` — `fix: expose only executable tracker capabilities`
5. `7362c2d` — `fix: restore cross-worker tracker single-flight`
6. `1b77fcb` — `fix: size ops uploads before reading`
7. `f1784cc` — `fix: preserve tracker SLA across workers`
8. `8f43911` — `fix: reject superseded shared cache flights`
9. `8958a6c` — `fix: serialize shared cache invalidation`
10. `f4a121c` — `perf: coalesce tracker status history`
11. `6e2c544` — `test: assert tracker history coalescing`
12. `2199185` — `fix: keep park scope usable when offline`
13. `0485cab` — `perf: combine emergency robot view reads`
14. `b49520e` — `fix: keep capacity benchmark CLI usable`
15. `6628cb6` — `test: count live merge namespace guard`
16. `55372ee` — `test: preserve park in management links`
17. documentation-finalization — audit/results/roadmap.

## Совместимость и конфликты

- Новых Alembic migrations, dependencies и environment variables нет; head
  остаётся `0030_user_activity`.
- Legacy Emergency routes сохранены.
- При конфликте cache-кода нельзя терять `shared_payload`, claim validation,
  namespace guard или status-history cache.
- Frontend меняет park scope/AppShell и EmergencyViewer; параллельные правки в
  этих файлах требуют ручного объединения.

## Проверка после merge

1. Выполнить `./scripts/verify.sh` в чистом checkout с Docker.
2. Проверить `python scripts/check-release-version.py --tag v0.1.42`.
3. Собрать release ZIP реальным Ed25519-ключом и проверить автономным
   `scripts/verify-artifact.py` доверенным public key.
4. Выполнить Wi-Fi/ARM/Tuna acceptance из `REMAINING.md`.
5. На тестовом хосте выполнить snapshot → update → readiness → restore.
