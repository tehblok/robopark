# План тестового выпуска: Khadas, AGX Orin и macOS

Цель: собрать локально проверяемый тестовый серверный выпуск для обоих типов
устройств и один установочный pkg macOS. Пользователь разрешил реализацию
13 октября; SSH предоставит позже либо установит самостоятельно. Роль сервера
выбирается при настройке, а не определяется моделью платы.

Спецификация: [macOS Emergency](2026-10-13-macos-emergency-design.md).
Стек: текущие FastAPI/PostgreSQL/Compose; агент Swift, Foundation, CryptoKit,
Security и WebKit без Python/Node/Docker на Mac пользователя.

## Контракты и границы

- API и broker запускаются раздельно; один broker владеет исходящими WSS Mac.
- Registry привязан к instance UUID и environment test/production. Одноразовая
  регистрация через пользовательскую сессию, затем P256 challenge signatures.
- Mac читает только фиксированный Emergency endpoint с собственной сессией;
  сервер проверяет права источника и получателя. Общие cookies не пересылаются.
- Текущий прямой Emergency остаётся без изменений при выключенном bridge.
- Native служба содержит локальный HTML UI, Keychain, reconnect, heartbeat,
  проверку подписанных обновлений и открытие Installer после выбора пользователя.
- Тестовый pkg без доступного Developer ID нельзя объявлять notarized. Подпись
  update metadata и Apple Developer ID — разные проверки.
- TLS заканчивается на текущем HTTPS origin; сквозное шифрование через Tuna
  пока не доказано. Это явная граница тестового выпуска, не выполненный пункт E2E.
- Дополнительные вычислительные jobs и внешнее файловое хранилище — следующие
  этапы; в этом выпуске распределяется чтение Emergency.

## Задачи и проверка

- [x] Backend (`apps/api`): регистрация/отзыв, scope, отдельный broker, RPC,
  ограниченный scheduler, интеграция provider. Сначала тесты single-use,
  несоответствия окружения, отзыва, 0/2/100 каналов и поздних ответов, затем код.
- [x] macOS (`apps/macos`): проверяемое ядро протокола/URL/update signatures,
  Keychain, вход Emergency через WebKit, локальная панель и фоновое соединение.
  Swift tests отвергают чужой instance, произвольный VIN/URL, подмену/откат/истечение
  update manifest; затем реальная сборка arm64 и проверка содержимого pkg.
- [x] Linux (`deploy`, nginx): внутренний broker, сохранение env/instance при
  обновлении, выбираемые test/production на Khadas/AGX. Targeted host tests,
  Compose config и отсутствие опубликованного broker-port.
- [x] Поставка (`scripts/build_macos_pkg.py`, `scripts/build_test_release.py`):
  версия из VERSION, один pkg, один OTA, checksum/metadata/инструкции. Ключи,
  базы и runtime-секреты не входят в артефакты.
- [x] Интеграция: существующие Emergency regression tests, host tests, Swift,
  сборка web/API/OTA, проверка миграции PostgreSQL и два API worker при наличии
  локального Docker. Независимое ревью security/correctness и исправление находок.
- [x] Отчёт: назвать реальные файлы и выполненные проверки, отдельно перечислить
  физическую приёмку Khadas/AGX, SSO и Apple notarization, если их пока нет.

## Особое внимание при проверке

1. Reconnect после сна: старое поколение не завершает новое задание.
2. Рабочий WSS при потере корпоративного доступа: агент снимается с выбора.
3. Права изменились после назначения: ответ не выдаётся без повторной проверки.
4. Оборванное/подменённое обновление: установленная версия и регистрация сохраняются.
5. Тестовый Mac/сервер не принимаются рабочим окружением из-за совпадения hostname.

## Ход выполнения

Начато: независимые backend и Linux deployment задачи делегированы, macOS и
интеграцию выполняет основной агент. Никакое физическое устройство не изменено.


Промежуточная проверка: Swift 43 checks, web production build и fast gate,
PostgreSQL 33 checks, Docker API build и nginx -t. Исправлены замечания по trust
серверов/каталогов/пакетов, Keychain recovery, role isolation и broker budgets.
Для общего пула требуется отдельное admin/royal approval устройства; это не
подменяет аппаратную attestation и доверие управляемому Mac. Финальные артефакты
собираются из локально зафиксированного commit, публикация/SSH не выполняются.

Итог к фиксации: 185 targeted API passed / 1 skipped, 43 Swift checks,
33 PostgreSQL tests. Независимое ревью: P0/P1 в заявленном pilot scope не осталось.
Сборка финальных файлов и проверка SHA/commit выполняются после local commit;
отчёт рядом с поставкой фиксирует результат этого шага.

Финальный полный host suite: 1493 passed, 8 skipped. PostgreSQL final: 33 passed.
