# Проверка тестового выпуска Mac Emergency

Версия: `0.2.0-rc.44.dev7114536505405942333`. Сборка локальная; физические
Khadas/AGX и установленный Mac-клиент не изменялись. Итоговые файлы и SHA-256
публикуются рядом в `artifacts/rc44-mac-test/release-manifest.json` и `SHA256SUMS`.

## Выполненные проверки

- Swift `AgentCoreChecks`: **43 passed**. Проверены canonical HTTPS/VIN,
  P256 DER challenge, несовпадение instance/environment, подпись/срок/поколение
  каталога, запрет понижения версий, целостность пакета, подписанный профиль
  сервера и запрет постороннего HTTPS-origin, точный SSO allowlist.
- Release arm64 build прошёл с deployment target macOS 13, `lipo -archs` проверен
  сборщиком. Pkg создан `pkgbuild`; app прошёл `codesign --verify --deep --strict`.
  `--version` возвращает полную версию выпуска. Bundle versions имеют числовой
  формат Apple; semver хранится отдельно.
- На реальном pkg проверены Ed25519 catalog signature, срок, канал, версия,
  размер, SHA-256 и совпадение embedded update root с явно переданным root.
- Web: `npm ci`, lint/check-nav и production `npm run build` прошли.
- API: широкий прогон дал 2993 passed, 4 skipped. 84 failures проверены отдельно:
  63 связаны с запуском не из `apps/api` и прошли из правильной директории;
  21 выявили устаревшую сигнатуру тестового mock. После её обновления весь
  `test_diagnostic_unknowns.py` — **36 passed**. Это последовательные проверки,
  не заявление о едином зелёном прогоне всех тестов после последней правки.
- Финальные целевые API/broker tests: **185 passed, 1 skipped**. Они покрывают регистрацию, scope, отзыв, limits,
  reconnect/late result, 0/2/100 реестровых соединений, cache/singleflight,
  anti-replay RPC, final authority, pending/admin approval и silent-first-agent failover. Отдельные результаты финального прогона
  сохраняются в `verification-summary.txt` рядом с артефактами.
- PostgreSQL: **33 passed** в disposable PostgreSQL 17. Первый запуск упёрся в
  заполненный диск Docker; повтор выполнен с `ROBOPARK_POSTGRES_TMPFS=1`.
  Это проверка транзакций/миграций, не тест долговечности хранилища.
- Финальный полный host suite: **1493 passed, 8 skipped**. Первоначально 1486 passed, 8 skipped; два failures были ожиданием
  старого migration head и общим запретом Ed25519 из удалённого Linux OTA
  workflow. Head обновлён, исключение ограничено новым Mac signing workflow,
  Linux hash-only OTA не менялся. Целевой повтор host/release tests — **37 passed**.
- Docker API image собран на linux/aarch64. Финальный broker проверен импортом
  актуального source в read-only Linux-контейнере. Compose config и `nginx -t` на
  закреплённом nginx 1.30.5 прошли. Брокер не публикует host-порт; у его WebSocket
  отдельный connection limit, отключённый broker не является обязательной
  зависимостью production web.

## Независимое ревью и границы

Исправлены замечания по подтверждению origin, env UUID, нормализации :443,
проверке каталога и встроенного ключа, stale update offer, startup profile refresh, downgrade, arm64,
Apple bundle versions, ограничению WebSocket frames/queue, блокировке event
loop, очистке singleflight после overflow и повторной проверке прав.

Выпуск предназначен для контролируемого стенда. Developer ID identities на
машине сборки отсутствуют: pkg не подписан Apple и не notarized. Ad-hoc app
подпись и Ed25519 metadata — разные проверки. Обновление ad-hoc app может
повторно запросить Keychain access; бесшовность ежедневных обновлений пока не
подтверждена. Полноценный corporate SSO и live failover между двумя Mac требуют
стенда с реальным доступом. 100 записей в тестовом registry не означают нагрузочную
приёмку 100 физических ноутбуков.

Одобрение администратора задаёт доверенный пул устройств; оно не доказывает
подлинность ответа с компрометированного Mac. Сквозное шифрование выше Tuna TLS,
attestation, новые вычислительные jobs, разгрузка файлового трафика Wi-Fi,
полный offline data client и проверка GPU/DLA в этот срез не входят.

Инструкция установки и подписания server profile:
[runbook](../runbooks/test-release.md).

Финальное независимое ревью после исправлений: воспроизводимых P0–P2 в
заявленном pilot scope не осталось. PostgreSQL после approval migration
перепроверен: 33 passed. Финальная сборка требует clean committed source и
сверяет embedded Mac commit с внутренним OTA commit.
