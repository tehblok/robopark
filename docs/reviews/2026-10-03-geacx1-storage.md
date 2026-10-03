# Проверка поддержки хранилища GEACX1

Дата: 2026-10-03. Исходная база: `7c1635c178991a6b2cb5cb4c436a1eecd89132a1`.
Кандидат: `0.2.0-rc.22`. Работы не выполнялись на production-дисках Khadas.

## Область

Два opt-in режима: ОС на NVMe и ОС на eMMC с рабочими данными на NVMe.
Самостоятельный bootstrap внутри OTA, независимая от приложения проверка
хранилища, systemd dependencies, защита host operations/OTA/rollback и удаления.
Хосты без manifest остаются unmanaged. Формат OTA v1 без подписи сохранён.

## Результаты

- Итоговый `./scripts/verify.sh host` на неизменном дереве после исправлений:
  **1108 passed in 75.58s**, exit 0. В него входят проверки установочных
  архивов, чистой установки, удаления, host operations, OTA, backup/restore.
- Новые проверки воспроизводят отказ mount/UUID/source/fsroot, readonly,
  заполнение диска, подготовку после прерывания, блокировку до Docker socket
  activation, сохранение mount roots при uninstall и standalone OTA bootstrap.
- Сгенерированные native units обеих схем проверены `systemd-analyze verify`
  в Ubuntu 24.04 с systemd 255.4; код завершения 0. Docker/containerd units
  представлены минимальными stub units; физический mount/boot не выполнялся.
- Пакет опубликованного rc.21 скачан из GitHub Release и проверен по SHA-256
  `eb2168458af4a6588c49f0f7100931bd690845bfff58ac5f69b2668014002092`.
  Все 57 Python-файлов Alembic совпадают с кандидатом побайтно. Head остаётся
  `0056_host_terminal`.
- `check-release-migrations.py`, release contract, tech-debt registry и module boundaries: успешно.
- Быстрый gate: **79 passed**, web lint и проверка 32 route IDs — успешно.
  Два предупреждения Starlette/httpx/anyio об устаревших API сохранены; это не новые отказы.
- Ruff новых storage-модулей/тестов, Python 3.10 grammar и host self-test — успешно.
- Все 12 поставляемых service units имеют storage dependencies в обоих режимах.

## Замечания независимого ревью

Исправлены блокировка очистки при low-space, повторная low-space проверка в
consumer/retention и аварийной обработке OTA, Docker discovery до проверки
mount при uninstall, потеря включения timer после ready-manifest, недостаточная
проверка source базового mount и позднее обновление OS guard перед restart.

Дополнительно исправлены взаимодействие с readonly namespace `ProtectSystem`,
порядок guards перед существующими ExecStartPre и разрешение updater/commands
обновлять независимый guard. Проверка использует host namespace PID 1;
намеренные service namespaces не ослабляются. Повторный interactive install
восстанавливает включение watchdog после прерванной подготовки. NVMe-root
проверяет источник каждого рабочего пути и отклоняет перенаправленные bind mounts
даже с совпадающим UUID. После RW mount повторно проверяется пустота NVMe: ext4
может восстановить файлы из журнала, невидимые при предыдущем ro,noload probe.

Очередь освобождается после отказа свежей операции из-за low-space, включая
legacy-команды и гонку после claim. Незавершённые эффекты с durable checkpoint
сохраняются при ошибке диска как во время исполнения, так и при reconcile.
Повторное восстановление после смены boot ID не требует свежего dispatch.
Эти сценарии воспроизведены регрессионными тестами; повторное независимое ревью
конкретных блокирующих замечаний не оставило.

## Не подтверждено этим отчётом

Нет проверки реальной GEACX1: vendor flash/UEFI, размер APP на SSD, холодная
загрузка с eMMC без NVMe, физические I/O failures, Wi-Fi reconnect, восстановление
SSH/VPN независимо от Docker, температурный режим и перенос реальной базы.
Секреты и данные не переносились. CUDA/TensorRT workload не добавлялся.

Код и синтаксическая проверка systemd не доказывают исправность оборудования
или production-готовность установки. Аппаратная приёмка — по
[runbook](../runbooks/geacx1-storage.md) и
[архитектуре](../architecture/2026-10-03-geacx1-storage-design.md).
Восьмичасовой тест не запускался. Опубликованный пакет rc.21 не заменяется.
