# GEACX1 Storage Implementation Plan

**Goal:** реализовать установку Robopark при ОС на NVMe и при ОС на eMMC с рабочим NVMe, сохранив существующие хосты и OTA.

**Architecture:** независимый stdlib guard хранится на системном диске; manifest фиксирует выбранную ФС. Подготовка создаёт только проверенные mounts на уже подготовленной пустой ext4, без форматирования. Приложение сохраняет прежние пути. Host operations, daemon units и OTA проверяют guard.

**Tech Stack:** Python 3.10+, systemd, ext4, Docker/Compose, существующий OTA v1.

**Spec:** [Схема двух вариантов](2026-10-03-geacx1-storage-design.md).

## Ограничения

- Без автоматического форматирования, изменения GPT, перепрошивки JetPack и действий над текущим production-хостом.
- Хост без manifest сохраняет прежнее поведение; символические ссылки не заменяют bind mounts.
- Manifest `/etc/robopark-storage/layout.json`, schema 1, root-owned 0600; независимый guard не зависит от `/opt/robopark`.
- Режимы `nvme-root` и `emmc-nvme-data`; состояние `preparing` блокирует обычный запуск до завершения подготовки.
- Физические boot/Wi-Fi/NVMe/thermal проверки и перенос реальных данных требуют доступной GEACX1; никаких восьмичасовых тестов.

## Критические сценарии проверки

- Отсутствующий/чужой/read-only NVMe не создаёт базу на eMMC и не блокирует загрузку самой eMMC.
- Пакетный запуск Docker, socket activation, ручной host CLI и timer не обходят guard.
- Прерывание подготовки оставляет блокировку записи и допускает повтор только с прежней идентичностью диска.
- Удаление не разрушает mountpoint/раздел или чужие данные; старый host agent не принимается на управляемой схеме.
- Восстановление сохраняет `SECRET_KEY` и проверяет Alembic head, не копирует профиль Khadas на Orin.

## 1. Discovery и guard

Файлы: `deploy/host/robopark_host/storage_layout.py`, `tests/host/test_storage_layout.py`.

- [x] RED: legacy без manifest; некорректный manifest; оба корректных layout; чужой UUID/подкаталог bind, отсутствующий mount, readonly, подготовка не закончена, мало места/inode.
- [x] Реализовать `StorageError`, `load_layout(root)`, `inspect_storage(root)`, `require_storage(root, require_layout=False, allow_preparing=False, check_space=True)` и standalone `check`/`inspect`.
- [x] GREEN: реальные парсеры используют фиксированные данные findmnt/lsblk, команды ограничены временем; запуск standalone с `python3 -I`.

## 2. Подготовка и systemd

Файлы: `deploy/host/robopark_host/storage_setup.py`, `tests/host/test_storage_setup.py`.

- [x] RED: отказ на непустом диске, работающем daemon, конфликтующих путях/units, неизвестном backend; повтор после interrupted prepare.
- [x] `plan_storage(mode, device, root)` возвращает проверяемый JSON; `prepare_storage(...)` повторяет discovery перед записью, устанавливает guard/drop-ins и manifest preparing до mounts, затем ready.
- [x] Создать необязательные для boot mounts и обязательные для writers зависимости; отдельный watchdog останавливает контейнеры без Compose при ошибках диска, не изменяет разделы.
- [x] GREEN: unit contents, порядок подготовительных команд и отказ без мутаций, fake-root lifecycle.

## 3. Установщик и пакет

Файлы: `deploy/ota/robopark_ota/{cli,host_install,remove}.py`, адаптер `storage.py`, `scripts/build_ota.py`, тесты OTA.

- [x] RED: CLI discovery/prepare; self-contained OTA до установки; existing prepared mounts принимаются только пустыми/проверенными.
- [x] Включить общий storage code в bootstrap OTA без зависимости от установленного приложения. Интерактивная установка предлагает оба режима; legacy остаётся доступным.
- [x] Guards до установки пакетов, извлечения payload и запуска контейнеров; preview/remove сохраняют корень mount и ownership-защиту.
- [x] GREEN: архив исполняется из временной директории, все прежние clean-install/remove сценарии проходят.

## 4. Host operations, OTA и состояние

Файлы: host CLI/state/runtime/OTA entrypoints, doctor; web/API — только если необходимы новые поля контракта.

- [x] RED: не происходит мутаций при отказе guard; legacy не меняется; кандидат без storage capability отклоняется до остановки сайта.
- [x] Добавить проверки перед write entrypoints и переключением релиза/rollback, сохранить drop-ins при замене units, отображать безопасный storage status в диагностике.
- [x] GREEN: точечные тесты операции/OTA/backup и полный host suite.

## 5. Документация и проверка

- [x] Описать обе установки, отсутствие форматирования, подготовку ext4, аварийный доступ, безопасный перенос ключа/БД и ограничения старого host agent.
- [x] Проверить Python lint/compile, host suite, OTA executable/package inventory, systemd units на Linux при доступном стенде.
- [x] Независимое ревью изменений, исправление замечаний и повтор затронутых проверок.
- [x] Зафиксировать реальные результаты и отдельно физические проверки, которые ещё не выполнены. Не заменять прежний опубликованный rc.21 пакет другими байтами.

## Аппаратная приёмка отдельно

- [ ] Проверить cold boot, отсутствие/отказ NVMe, Wi-Fi, независимый аварийный доступ и перенос/restore на GEACX1. Плата в текущем окружении недоступна; автоматические тесты не закрывают этот пункт.

Проверки реализации и ограничения — в [отчёте](../reviews/2026-10-03-geacx1-storage.md).
