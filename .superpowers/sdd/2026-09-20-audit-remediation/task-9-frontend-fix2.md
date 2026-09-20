# Task 9 frontend/docs review fix 2

## Scope

- Admin user location availability now uses the same public-IP eligibility
  check as the lookup resolver. With the explicit `ipwhois` provider,
  missing, malformed and non-public addresses report `no_ip`; a public address
  without a result reports `unavailable`; stored locations report `available`.
- Installer, deployment and progress documentation now record the real Task 9
  v1-to-waiting-v2 production-PWA smoke without presenting it as release
  acceptance for the exact final source hash.
- OTA signature behavior and acceptance evidence were not changed.

## Root cause and TDD evidence

`admin_users._user_out` classified any non-empty `last_ip` as eligible while
the resolver uses `user_activity.public_ip`. A targeted API test covering a
private address failed first with `unavailable` instead of `no_ip`. The
serializer now calls the shared eligibility function.

## Verification

Executed from `apps/api`:

```sh
.venv/bin/python -m pytest -p no:cacheprovider -q tests/test_user_activity.py
.venv/bin/ruff check src/robopark_api/routers/admin_users.py tests/test_user_activity.py
```

Result: `9 passed, 1 warning in 0.93s`; Ruff passed. The warning is the existing
Starlette/httpx TestClient deprecation warning.

Executed from the repository root:

```sh
git diff --check -- apps/api/src/robopark_api/routers/admin_users.py \
  apps/api/tests/test_user_activity.py deploy/INSTALL-ARMBIAN-RU.md \
  deploy/README.md docs/product-completion/PROGRESS.md
```

Result: passed.

No production-PWA rerun, full suite, regular E2E, Docker/PostgreSQL, load,
soak, installer/VM, OTA, or signature command was run.
