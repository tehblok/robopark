# Royal terminal — реализация и приёмка

Дата: 2026-10-02. Статус: v11 `0.2.0-rc.19.dev6983770337244419265` успешно установлен на сайте, операция d1206062-f75b-42b2-a962-1d84f0d7f45b завершена. Core healthy, BuildKit1,809,959,074B и blocked=false. Live terminal всё ещё недоступен (`terminal_unavailable`); причина уточняется по фактическим host config/projection/unit status. Ранее Linux проверял прямой движок OTA и реальную API-container→broker границу, но не website typed consumer/systemd sandbox. Полная live WSS-проверка не выполнена. Весь сайт нельзя считать «без багов».

## Разрешённый объём

Полный Linux-терминал для royal: обычный Unix-пользователь, отдельный root через пароль и свежий TOTP. Формат OTA v1 с SHA-256 сохранён. Восьмичасовой тест не выполняется. Пользователь дополнительно попросил разобраться с непонятными размерами Docker и недоступной очисткой.

## Исправления по результатам проверки

- Замороженный runtime rc.16 не мог подготовить пакет с новым PostgreSQL image: первая foundation ступень сохраняет 17.6, новый runtime явно получает нужный образ БД перед pin/build следующей ступени.
- Alembic блокировался maintenance guard. Исключение действует только для выделенного migration engine; обычные записи приложения во время обслуживания остаются запрещены.
- Ошибка ACK при attach теперь запускает disconnect deadline; повторный DELETE закрытой/отсутствующей broker-сессии идемпотентен.
- Maintenance не принимает никакие supplementary groups; проверяется при setup и перед каждым worker IPC. Это закрывает обнаруженный ревью обход через группу `disk`.
- Диагностика экспортирует только строгий список lifecycle полей, без descriptor, команд, вывода и auth hashes.
- Default storage probe теперь использует тот же private Docker config, что updater. Раньше отдельный BuildKit мог не измеряться из-за другого профиля. Объём Docker Local Volumes нельзя целиком считать мусором: туда входят постоянные и служебные хранилища.
- В feature v1 реальный nginx отклонил некавыченный regex WebSocket. Кандидат не дошёл до cutover; старый foundation остался healthy. Пакет v1 отвергнут, переиспользовать для установки нельзя.

## Доказательства

| Проверка | Результат |
| --- | --- |
| Frozen rc.16 clean install → accepted foundation через настоящий старый updater | PASS; DB0055, 4 core containers healthy, terminal отсутствует |
| Feature v1 через installed foundation updater | FAIL до cutover; nginx regex; cleanup успешен, foundation healthy |
| Полный host unit suite | Новый полный прогон: 994 PASS, 0 FAIL (82,32с); прежние результаты сохранены в истории |
| Поздние host fixes/protocol/diagnostics + metadata/docs | PASS профильными тестами; логи в audit |
| API auth/admin/terminal/sessioncleanup/migration regression | 128 PASS |
| PostgreSQL concurrent reservations/tickets, BIGINT schema | 3 PASS, отдельный удалённый по завершении test container |
| API shared wire fixture | 1 PASS |
| API idempotent-close fixes + terminal stream | 21 PASS |
| Web после финальных review fixes | 2742 PASS; targeted103 PASS; lint/build PASS; предыдущий e2e PASS |
| Module boundaries / migration declarations / shell syntax | PASS |
| Независимое ревью | 4 findings исправлены; focused recheck host/API/storage и web без новых находок (29 host / 10 API / 93 web PASS) |
| Реальный Linux PTY/cgroup/root900s | v8 PASS 9/9: normal/PTY/isolation, disconnect15, lease20, flood, restart, root и настоящий900s. Root persisted expired, worker inactive/dead, MainPID0, cgroup отсутствует |
| Rollback feature → foundation | v2 первоначально healthy, но повторное OTA выявило dangling previous и stale DB tables. Оба дефекта исправлены; v6 полный rollback и проверка их отсутствия PASS |
| Live foundation / feature / WSS | Foundation и v8 установлены / terminal_unavailable / WSS NOT RUN |
| 8h soak | NOT RUN по явному указанию пользователя |

## Пакеты

Принятый foundation: `0.2.0-rc.17.dev16437318597787721811`, только из frozen `0.2.0-rc.16.dev11074319897666147461`.

- Outer SHA256: `6e491fdd7632a50479b7fceb8ce25fbc4b3f3efee8570441b57adfa077cbfc9b`.
- OTA SHA256: `13245d82b1c2b5670f626a151040161a8d32bbb25c27588b0af6a2d820a233ec`.
- Provenance: `output/audit/2026-10-02/royal-terminal/foundation-v2/final-provenance.json`. Реконструированная исходная foundation использует явно обозначенный synthetic Git baseline; это не commit основного репозитория.

Отвергнутый feature v1: `0.2.0-rc.18.dev17309660336588615524`, SHA256 `88c19dd6516aa7e067c9ae93e8d3b0d24cd9df8cd0c223a6b88e7f0b29d91045`. Сохранён для воспроизведения сбоя; не устанавливать на production.

## Ограничения

API является доверенным делегатором root. Полный RCE в API даёт возможность обратиться к broker; этот релиз не меняет такую границу доверия. Root не является sandbox, и TTL не отменяет выполненные изменения/намеренно созданные внешние службы. Сайт/туннель должны работать для доступа к терминалу. Формат hash-only OTA не аутентифицирует издателя, согласно сохранённому решению пользователя.

Подробные правила эксплуатации: [runbook](../runbooks/royal-host-terminal.md). Исходные FAIL сохраняются рядом с PASS, чтобы отчёт не скрывал обнаруженные дефекты.

## Исправленный кандидат v2

`0.2.0-rc.18.dev13131185508027698148`, совместим только с accepted foundation. Outer SHA256 `3e047fbbb6d2a345639c8608940323d305d84777a6fe4a8ea0f8b254c536040b`; inner OTA SHA256 `98cf76d2382df0bead9adbb899786578b28125b1667b3a7c7b10855571c71019`. Установка PASS: DB0056, published, core healthy. Приёмка FAIL: release/deploy/host имеют 0700, maintenance не может прочитать entrypoint. Пакет отвергнут для production. Полный rollback PASS; evidence в feature-v2. Default storage probe подтвердил отдельный BuildKit: 2145737343 bytes, значение получено на тестовом хосте, не на production.

## Кандидат v3

`0.2.0-rc.18.dev4573027954411373549`, outer SHA256 `8e7db49737d728e939e0830737e74a748ddc7d8a7e79608f6c17ed25c60ef020`. Setup собирает отдельный root-owned worker.pyz (0440, группа robopark-maint), включающий только worker/protocol/setup и фиксированный entrypoint. Закрытые каталоги релиза сохраняют права; worker получает только код для запуска PTY. Setup заново выполняется при подготовке следующего релиза и останавливается при quiesce. RED2 → GREEN46 профильных host tests. Первый прогон v3 остановился на snapshot и автоматически вернулся на healthy foundation (`ota_snapshot_failed`); источник исключения выясняется диагностическим повтором на VM. Независимое ревью worker bundle: без замечаний, 112 тестов PASS. Реальный root900s ещё не запускался.

Последние профильные проверки: 81 host PASS, 21 terminal API PASS, 21 admin OTA PASS. Расширенный API прогон был остановлен для диагностики ошибок внешнего disk budget в admin_ota unit fixtures; весь прогон не заявлен успешным. Модуль воспроизведён отдельно, fixture сделана детерминированной, все 21 его тест прошли. Production disk thresholds не менялись.

Live foundation подтверждён панелью «Версия системы»: `0.2.0-rc.17.dev16437318597787721811`, build `13245d82b1c2b567`; screenshot `foundation-installed.jpg`. Старый health report rc16 помечен интерфейсом как устаревший и не выдаётся за свежую проверку rc17.

После сборки v3 уточнена одна UI-подсказка очистки: открыть предпросмотр, либо объяснить blocked/пустой результат. 82 SystemPage tests, web lint/build PASS. Эта web-only правка потребует финального rebuild; v3 не является финальным пользовательским пакетом.

## Причина сбоя повторного обновления и кандидат v5

Диагностический повтор v3 показал `FileNotFoundError` при разрешении `/opt/robopark/previous`: после publish старый grandparent rc16 был штатно удалён retention, а rollback v2 восстановил ссылку на него из сохранённых metadata. До pg_dump выполнение не дошло. Общий receipt `ota_snapshot_failed` сам по себе не доказывает, какой шаг snapshot завершился ошибкой. В тестовой VM после сохранения evidence удалена только эта dangling symlink; текущий foundation и данные сохранены.

Общий helper восстановления previous теперь сохраняет ссылку только на существующий каталог релиза; отсутствующий retired grandparent означает отсутствие дальнейшего rollback target. Подменённые symlink/file/escape targets отклоняются. Исправлены оба rollback пути. Регрессия RED2 → GREEN104 OTA/bootstrap tests; дополнительно99 host/restore/image tests PASS.

Кандидат v5: `0.2.0-rc.18.dev1894085360477702012`, outer SHA256 `3b356dae381f39670df9450f40f55d17d47bc92b225f9cddce9c448426f58285`. Включает worker runtime, rollback fix и окончательную подсказку очистки. При последующей Linux приёмке v5 отвергнут из-за stale DB tables после старого rollback, см. ниже. v3/v4/v5 не являются пакетами для production.

## Повторная миграция после rollback и кандидат v6

v5 при повторной миграции встретил `DuplicateTable`: rollback v2 оставил три пустые таблицы0056 при возвращении alembic_version0055. `pg_restore --clean` удаляет только объекты, имеющиеся в dump. Исправление в двух локальных OTA rollback путях пересоздаёт базу из доверенного локального dump: `--clean --if-exists --create --exit-on-error --dbname=postgres`. Внешний/manual restore не изменён. [Семантика PostgreSQL17](https://www.postgresql.org/docs/17/app-pgrestore.html). Короткий реальный PG17 probe с production-эквивалентным `pg_dump --format=custom` без `--create`: старые данные восстановлены, новая таблица удалена, основная fixture DB не затронута. Затем только три пустые stale terminal таблицы удалены из fixture после сохранения evidence; данные foundation сохранены. Это repair тестового состояния, не патч установленного продукта.

Независимое ревью дополнительно подтвердило ошибку лимита кэша: Docker MemBytes интерпретирует `2GB` как2GiB, а код проверял2e9. Теперь CLI и post-check используют одно точное значение2000000000 байт. [Docker CLI](https://github.com/docker/cli/blob/master/opts/opts.go), [go-units](https://github.com/docker/go-units/blob/main/size.go). RED3→GREEN граничных проверок; итоговый профиль159PASS, restore/OTA профиль180PASS. Оба исправления прошли независимое ревью без остаточных замечаний.

Кандидат v6: `0.2.0-rc.18.dev10013030721945842647`, outer SHA256 `f074e04d5e7c86d39e41fb4df42c15cc23bb50bc7522134317588ea97d80355d`. Передан на реальную Linux установку/приёмку. Не считать TESTED/INSTALLED до соответствующей записи evidence. v5 оставлен только для истории диагностики.

Итоговый полный host прогон v6: 959PASS/1FAIL (один mock ожидал старый текст CLI2GB). Обновлено только ожидание теста; весь затронутый systemd module29PASS. Это не один полностью зелёный full log: исходный FAIL сохранён рядом с отдельным recheck. Product payload после v6 не менялся.


## Причины завершения и кандидат v8

v6 реально останавливал потерявший lease worker за20,25с, но гонка с watchdog записывала `shell_exited`. Broker теперь определяет уже наступивший deadline под тем же lock до остановки; ранний добровольный выход остаётся `shell_exited`. RED3→GREEN27. v7 установлен в VM; focused lease PASS за19,98с, persisted `lease_expired`, затем rollback на foundation.

Повторное ревью выявило округление оставшегося срока вниз: worker мог выйти на долю секунды раньше абсолютного deadline. Worker budget теперь округляется вверх и ограничивается первоначальным разрешённым сроком; точный monotonic deadline продолжает контролировать broker. RED2→GREEN44, Ruff PASS, независимое ревью без замечаний. Оба профиля покрыты дробными моментами запуска.

Финальный кандидат v8: `0.2.0-rc.18.dev10416094313782083181`, outer SHA256 `f83a3fea283da5957d2e585c6a19fe4056ccc78ce5abd598134ee2c37700b6a2`, только из accepted foundation. Полный Linux прогон PASS9/9; настоящий root900s PASS. Reboot smoke и actual rollback/reapply PASS. Пакет проверен live сервером, ожидает TOTP владельца; feature ещё не установлен на production.


Приёмка v8: root создан2026-10-02T01:42:02.246988Z, завершён01:57:02.563251Z; измерение после attach899,96с, полный check900,37с. Persisted `expired`; точный worker inactive/dead, MainPID0, ControlGroup пуст, cgroup отсутствует. Disconnect15,38с, lease20,08с (`lease_expired`), output flood1,03с. Maintenance60min и idle10min проверялись ускоренно управляемыми монотонными часами установленного runtime. Это не реальные часовой/десятиминутный прогоны. Evidence: `feature-v8/terminal-acceptance-v8.json`, `root900-worker-lifecycle.txt`, `root900-cgroup-paths.txt`. Inner OTA SHA256: `828d1cfa3284fc86721b66b326e2c1a15127913873483187095fb8371921383b`.

Live подготовка: сервер проверил inner OTA с SHA256828d1cfa3284fc86721b66b326e2c1a15127913873483187095fb8371921383b, targetrc18/fromacceptedrc17. Пароль и UPDATE ROBOPARK заполнены, свежий TOTP и нажатие установки переданы владельцу. Screenshotfeature-v8-live-ready.jpg сделан до ввода кода. Live WSS и обновлённое отображение хранилища ещё NOT RUN.

Post-budget проверка установленного v8 runtime PASS: attempted=true, blocked=false, owned BuildKit reported/private reclaimable1 851 403 553 bytes при лимите2 000 000 000. Docker Engine cache0, Local Volumes1 806 000 000; категории могут пересекаться, volumes не считаются reclaimable. Эти числа относятся только к isolated VM. Reapply опубликован2026-10-02T02:02:06Z, DB0056, brokeractive, все4 corecontainershealthy. Reboot smoke подтвердил root и units; отдельный повторный boot + maintenance_profile_and_pty PASS: обычный UID, пустые capabilities, no_new_privs, home write, запрет protected/private paths и broker socket, UTF-8/resize/Ctrl-C, cgroup cleanup. Evidence: feature-v8/reboot-maintenance-smoke.json. Контрольные суммы evidence проверены. VM остановлена с сохранением диска.


## Live v8: API bridge and BuildKit budget

v8 установился: версия0.2.0-rc.18.dev10416094313782083181, build828d1cfa3284fc86, операцияb62cd305-3291-45b2-82e9-52a8a9e00d17. Но capabilities возвращает terminal_unavailable. Linux контейнерный probe воспроизвёл: host broker и projection исправны, API не получил TERMINAL_BROKER_SOCKET и bind каталога сокета. _render_configs проверял наличие terminal payload по final candidate до переименования staging. Исправляется проверкой source_release=stage. Для первого обновления со старого v8 renderer boot-preflight нового runtime строго дополняет только API в root-owned pinned current Compose после restore recovery. Активный restore handoff исключён, конфликтующий/небезопасный config блокирует запуск.

Live storage snapshot показывает2013704446 байт при лимите2000000000 и blocked=true без общей нехватки диска. Это результат старой команды2GB, допускавшей2GiB. Точное значение байт уже исправлено в v8 runtime; следующая публикация должна выполнить исправленную очистку. Проверить по свежей live projection, а не по VM результату.

Новая API-boundary приёмка на установленном v8 дала ожидаемый FAIL за1.18с до создания PTY. Она проверяет путь из фактического API-контейнера через installed BrokerClient после этапа authorization; HTTP auth и live WSS остаются отдельными проверками. Старые9/9 host runtime результатов и настоящий900s сохранены, но не выдаются за end-to-end терминала.

Корректирующий v9: `0.2.0-rc.19.dev11318626615568679682`, совместим с установленным v8. Outer SHA256 `cd0196bc55747650627f798787b73c4ef3c0778f19c9b30b21c6b5e13f11a413`, inner SHA256 `6ccef69c5ed4b9079b26030dd6af45ef88d1919237ab2f8aefe103ea1500120d`. Продуктовый diff ограничен четырьмя host-модулями и metadata версии. Полный host suite991PASS, focused167PASS, независимое targeted review46PASS без findings.

Настоящий installed-v8 updater → v9 published PASS. В API-контейнере UID10001 установлены env и RO bind; capabilities/socket/projection PASS. Обе PTY через installed API BrokerClient: maintenance UID997, root UID0, UTF-8/resize/cleanup PASS. Первый запуск нового probe ошибочно сравнивал вывод id как одну строку; ошибку fixture исправили и сохранили исходный FAIL. Затем reboot+API-boundary PASS, настоящий rollback v9→v8→повторное OTA v9 и API-boundary PASS. Root900 не повторялся, broker/worker не менялись.

Свежий BuildKit после first-hop:1971769623 байта при лимите2000000000, attempted=true, blocked=false. Это VM, не live-хост. Live пакет загружен, SHA совпал и серверная проверка прошла; пароль/фраза подготовлены, владелец должен ввести свежий TOTP и нажать установку. Screenshot: `feature-v9-live-ready.jpg`. После этого остаются live версия/receipt, свежий storage report и браузерный WSS.

Live установка v9 после подтверждения владельца завершилась `ota_stage_failed`, операция `0b844a6d-6880-48e4-81dc-6ae0c23624cc`, 03:11:29Z 2026-10-02. Сбой до snapshot/migrate/cutover; сайт продолжает отвечать. Точную причину этот токен не раскрывает. Открыта штатная форма DIAGNOSTICS, ожидается свежий TOTP владельца.

Проверка именно shipped v8 выявила ограничение диагностики: combined stdout/stderr первого сбоя Docker сохраняется в `/var/log/robopark/ota-update.log` (до2MiB,0600), но файл не входит в diagnostics bundle и не дублируется в journald. Архив содержит update-status, состояние и последние200 journal записей robopark/service, tuna и updater; он может дать косвенные сведения, но не заменяет отдельный OTA-лог. Не считать VM PASS подтверждением live успеха и не повторять установку без новых данных о причине.

Уточнение после доступа владельца к терминалу: указанный OTA-лог отсутствует. В предыдущем анализе был пропущен отдельный путь web OTA: `_production_typed_effects` создавал `SystemRunner()` без `failure_log`, тогда как legacy update CLI подключал его. Это подтверждено внутри exact shipped v8. Подключение исправлено в исходниках; реальный failing child regression RED→GREEN проверяет запись0600 и сохранение первой ошибки, профиль209PASS, полный host992PASS. На хосте исправление ещё не установлено. Обычная диагностика дополнительно отбрасывает MESSAGE из journald, поэтому её повторный сбор не восстановит потерянный вывод.

Первопричина live сбоя теперь подтверждена историей BuildKit, полученной владельцем: web `RUN npm ci`, `npm error code ETIMEDOUT`, syscall read, errno-110, exit146 через20,49с. Время npm log03:11:04.188Z совпадает с операцией OTA. Скачивание metadata базовых Node/nginx images прошло, затем прервалась загрузка npm dependencies. Это не доказывает конкретную причину сетевого сбоя (маршрут/proxy/registry), но локализует упавшую команду. Подготовка исправления: persistent npm cache в собственном BuildKit и bounded retry только известных сетевых ошибок с сохранением lock/integrity/TLS. [npm configuration](https://docs.npmjs.com/cli/v11/using-npm/config/), [Docker cache mounts](https://docs.docker.com/build/cache/optimize/).

## Кандидат v10: устойчивость установки зависимостей

Сбой v9 подтверждён журналом BuildKit, полученным владельцем хоста: `npm ci` завершился `read ETIMEDOUT` через 20,49 с, код146. Получение метаданных Node/nginx прошло. Причина сетевого разрыва неизвестна; npm, зависимости, registry и TLS не менялись.

Добавлены максимум три полных попытки `npm ci` только при структурированных временных сетевых ошибках, с паузами10/20с. Ошибки lockfile, integrity и авторизации завершаются сразу. Npm-cache хранится внутри собственного BuildKit и учитывается общим ограничением его кэша. Новый shell-helper включён в точечный список допуска исходников пакета; тестовый файл исключён. Проверки:8 поведенческих npm-тестов,31 полный scripts suite,38 packaging tests PASS. Подключение failure log к webOTA consumer проверено отдельным RED/GREEN; полный host suite992PASS. Это исправление ещё не установлено на сайт и не добавляет экспорт сырого журнала в диагностику.

Версия `0.2.0-rc.19.dev8874664270371276108`, только с установленной v8. Outer SHA256 `32d1ba883a41c8a47ad5eab0d39ff86cc9ab674d0705d97faa95a6d4f547cb42`; inner SHA256 `a0fe2a2ec88d9b44a5c57da0042da26f280c4177a6b9a7572f16f5b5f4f0d326`. Linux acceptance и независимое ревью выполняются. Прежний временный диск VM исчез; новая изолированная Ubuntu22.04 восстанавливается через frozenrc16→foundation→точнуюv8. Результаты старой VM сохраняются отдельно, новая проверка имеет отдельное происхождение.

V10 rejected before deployment after review: a lifecycle line could trigger retry despite permanent npm code; handled missing-builder inspect could occupy first-failure log. Both reproduced and fixed. Final v11 `0.2.0-rc.19.dev6983770337244419265`, outerSHA `244a0c650edadedc31ea5def0ee7a4b3df4c8d7fc430953f5bffa37758153906`, innerSHA `86a8d8dc2eaa97355087aa68763d0785454c9b46508b3b905ef10a5e2f236902`. Full host994PASS (82.32s), scripts32PASS, independent re-review no findings (installer9/owned-builder10/logger3). No dependency/lock content change except release version. Reconstructed fixture accepted foundation PASS; exactv8 installation in progress, then v11 Linux acceptance. Live v8 unchanged.

V11 final Linux acceptance PASS: first-hop58.30s from exact installedv8, API boundary maintenance/root PASS; reboot and same boundary PASS; rollback exactv8 PASS; reapply36.95s and boundary PASS. Separate disposable builder used exact installed Dockerfile for cold/warm cache-mount proof (27s/19s total, dependency step5.0s/3.5s with invalidated layer), then removed. Owned builder post-budget1,910,382,653B<=2e9, blockedfalse. Harness8PASS; ACCEPTANCE-SHA256SUMS verified by fixture agent. Live upload/server verification exactinnerSHA86a8... passed; form/password/phrase ready, owner freshTOTP+clickinstall requested. Live stillv8 until operation success; WSS notrun. No8h/no root900 repeat.

Live v11 install SUCCESS: operation d1206062-f75b-42b2-a962-1d84f0d7f45b created2026-10-02T14:56:30Z/completed15:01:30Z; release rc19.dev6983770337244419265/build86a8d8dc2eaa9735 confirmed. DB/container/Tuna healthy, owned builder1,809,959,074B blockedfalse; cleanup deferred whileOTAownedlock. Terminal liveFAIL persists availablefalse/terminal_unavailable; no session opened, WSSnotrun. Acceptance scope correction: previous fixture used OtaUpdateEngine(SystemOtaUpdateRuntime).apply directly, NOT website typed command consumer/service sandbox; post-update API/broker probes were real but do not establish initiation-path coverage. Frozenv8 render initially omitsbridge; newv11 bootrepair should run asfreshpython inExecStartPre. Explorer's conclusion that oldCLI necessarily runs afterhosttoolsswitch is NOT established and conflictswithfreshprocess semantics. Need actual host env/bind/projection/unitstatus; owner one-line readonlycommand requested. Fixture attempts exact typedservice repro, currently host virtualizationavailability error, durableVMdiskretained. No productfix basedonguess.

Exact typed-consumer reproduction gate PASS after restarting durableVM with authorized VZ execution. Rollback to exactv8 reproduced missing APIenv/bind and unavailablecap despite livebroker/projection. Valid fixture-only root-approved inbox OTA request b09278dd-1e39-431c-8843-555a3451d5db consumed by actual robopark-commands.service with productionfactory/systemdprotections; published/succeeded in39.65s. Afterward compose/API env+RO bind and UID10001 capabilities available. Public HTTP grant issuance excluded; roottrustedfixtureapproval explicitly used onlyinVM. Thus the prior coverage gap is closed but NOT causal for live terminal_unavailable. Working-host read-only config/projection/unit-status requested and pending; do not claim rootcause or fix from source-only hypothesis. Evidence typed-consumer-gate/. No product changes during this investigation.

### Live setup failure: host evidence

Owner-provided host output confirms the API socket setting and read-only bind are present. Public projection reports `terminal_setup_failed`; setup is failed/exit1, broker inactive after its dependency failed. Trace points to the first `runuser ... worker.pyz --help` check in `terminal_setup.py:151`. `SystemRunner()` has no failure log in this CLI branch and replaces the child output with the misleading generic `docker_command_failed`. A PAM `pam_keyinit` UID-change warning accompanies the failure but is not sufficient to establish its cause.

The same worker help succeeds both directly as `robopark-maint` and in a transient service with NoNewPrivileges/PrivateTmp/ProtectHome/AF_UNIX and a clean child environment (exit0, 462ms). Restarting the actual setup still failed according to the owner's follow-up; its complete unit file matches the source and contains no displayed drop-ins. Next probe repeats full setup with the remaining unit properties and prints the bounded failing-child output, without editing installed sources or relaxing protections. Cause remains unconfirmed; live PTY/WSS has not passed. No product changes or repeated long-duration tests during this diagnosis.

Full-setup transient probe also PASS: `robopark-terminal-setup-debug.service`, exit0, runtime1.168s/CPU1.137s. It invoked the installed launcher via runpy with the real SystemRunner and all explicit setup protections/runtime-directory properties, changing only the in-memory failure logger to print bounded child output. No child failure was printed. This performed the real idempotent preparation, so one subsequent ordinary setup/broker start is requested to determine whether the resulting host state now works. If it still fails, compare effective unit/process context and capture the original unit's child output; do not infer that the transient success fixed the cause.

### 2026-10-03: missing CAP_SETUID in the original setup service

The owner's final collector report identifies the failing child exactly: `runuser: cannot set user id: Operation not permitted`. The original setup parent has UID0, CapPrm/CapEff `000001ffffffff7f` (CAP_SETUID absent), CapBnd `000001ffffffffff` (present), CapInh `0000000000200100`, NNP1 and seccomp2. Host: Ubuntu24.04.3, systemd255.4-1ubuntu8.10, kernel5.15.137/aarch64. Diagnostic override removal is confirmed; original setup remains failed and broker inactive. Source report and decoded bytes are preserved under `output/audit/2026-10-03/royal-terminal-capabilities/`.

Upstream [systemd v255 exec-invoke.c](https://github.com/systemd/systemd/blob/v255/src/core/exec-invoke.c) drops CAP_SETUID in the explicit-user/seccomp path unless it is ambient. Its retained CAP_SYS_ADMIN/CAP_SETPCAP also match the observed inheritable mask. System services default to root without `User=`; the successful transient probe had omitted this redundant declaration. Candidate removes only `User=root` from setup, keeps the existing protections and explicit root validation in Python, and leaves both terminal profile units unchanged. A new Ubuntu24/systemd255 fixture is preparing the actual A/B test; existing Ubuntu22 acceptance does not cover this behavior.

The setup CLI now attaches a private first-failure log using the existing bounded SystemRunner logger. Regression with a real failing child: RED (file absent) → GREEN, 13 setup tests; focused setup/lifecycle/bootstrap/unit/compatibility suite65 PASS, Ruff PASS. Full host suite, independent review, artifact and Ubuntu24 checks are pending. No new package has been deployed.

Corrective v12 is frozen as `0.2.0-rc.20.dev11411368079194000089`, compatible only with the installed v11 `0.2.0-rc.19.dev6983770337244419265`. Inner SHA256 `7bf818605ab8996f948adfcb758f432a97dcf38e8f40b634204df4b8db4363ce`; outer SHA256 `a6d84f1ddadc73b4d9ec41438717346a45666d465ff945932f42967a9bd835d0`. The product delta is exactly the setup unit and CLI failure-log wiring; other archive changes are generated version metadata. OTA v1 and migration 0056 are unchanged.

Full host suite995 PASS in80.25s; independent production review has no findings. Real artifact A/B on Ubuntu24.04.5/aarch64/systemd255.4-1ubuntu8.17 reproduced v11 failure and v12 success, with matching NNP/PrivateTmp/ProtectHome/AF_UNIX/UMask/LimitCORE. Candidate retained UID0/CAP_SETUID and completed both ordinary-user readiness checks. The repeatable guarded gate is `tests/terminal_linux/systemd_setup_probe.py`; fixture instructions and exact reports are under `output/audit/2026-10-03/royal-terminal-capabilities/`. This VM uses kernel6.8; the live host remains kernel5.15, so live validation is still required.

Live browser upload and server verification passed for the exact v12 inner hash. The confirmation form is prepared, but no installation has been submitted. Installed-runtime OTA/rollback acceptance and owner fresh TOTP remain pending; no new live PTY/WSS success is claimed.

Final v12 gates PASS: Ubuntu24 artifact regression also asserts empty effective AmbientCapabilities for both versions; no ambient capability was added. Existing Ubuntu22 fixture consumed the exact v12 request through `robopark-commands.service` and published it in62.49s. API container → broker → maintenance UID997/root UID0 PTY passed. Actual rollback restored exact v11, followed by successful typed-consumer v12 reapply and another API-boundary PASS. The first immediate reapply after rollback was safely rejected as `capabilities_changed`; after the normal watchdog refreshed the projection, a fresh request succeeded. Both receipts are retained, without hiding the rejected attempt. Evidence: `feature-v12/typed-consumer/` and `royal-terminal-capabilities/systemd-setup-probe.json`.

These are isolated VM results. Public HTTP grant issuance and live WSS remain pending; fixture approval metadata was generated only inside the marked disposable VM. No 900-second repeat or 8-hour run. Product artifacts remain unchanged; live remains v11 until the owner submits the prepared OTA form with fresh TOTP.

Live v12 installation confirmed after owner submission: admin version `0.2.0-rc.20.dev11411368079194000089`, build `7bf818605ab8996f`; OTA operation `70698b8c-3763-4500-941e-59fb4795d8bb` displays completed. Current rollback target is exact installed v11. System page shows PostgreSQL/containers/Worker/Tracker/Tuna working. Terminal capability response is HTTP200/`available=true`, profiles maintenance/root, active_sessions0, generated `2026-10-02T23:06:09.449606+00:00`; the former setup-unavailable failure is no longer present. Saved response: `feature-v12/live-terminal-capabilities.json`.

The ordinary-session confirmation form is open with password filled and TOTP empty. Owner fresh TOTP + submit requested because terminal authorization is separate from OTA. No live shell commands or WebSocket/PTTY success claimed yet. Screenshot: `feature-v12/live-terminal-ready.jpg`.

### Live terminal acceptance completed 03.10.2026

Owner entered separate fresh TOTP for maintenance and root. Maintenance WSS101, UID997/GID988/CapEff0/NNP1, UTF8, temporary HOME file, Ctrl+C, denied shadow/socket access PASS. Root UID0/full effective capabilities and actual host service checks PASS. Both sessions explicitly closed through the UI. Live evidence and subsequent host audit: [03.10 live report](2026-10-03-live-host-audit.md). These observations replace the pending live-session status above; no permanent root session remains.
