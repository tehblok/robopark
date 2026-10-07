# Проверка parity штатного Telegram-бота

Дата проверки: 2026-10-07.

Эта матрица сравнивает функции старого `tracker-report-server` со штатным
Telegram-сервисом Robopark. Статус «готово» означает, что код и узкие тесты есть
в выпускаемом коде. Итоги локальных проверок приведены ниже. Хост выключен;
установка на нём и реальные Telegram-отправки пока не проверены.

| Функция legacy | Штатный путь Robopark | Статус и границы проверки |
| --- | --- | --- |
| Собственный список пользователей, администраторов и разрешённых Telegram ID | Пользователи, роли, активность и назначения парков ведутся каноническим web UI и существующими User/UserPark/RBAC API. Telegram ID только связывает уже существующую учётную запись. | Готово. Старые whitelist/admin ID намеренно не импортируются и не повышают роль. |
| Запрос доступа механика/оператора | Одна ParkRequest-модель обслуживает сайт и Telegram. Администратор принимает решения только по своим паркам; royal сохраняет отдельный глобальный workflow. Несколько администраторов одного парка поддерживаются row lock/CAS. | Готово. Проверены pending/link/request/approve, ограничения чужого парка и защита rejected-пользователя от самовосстановления. |
| Отдельные настройки и секреты бота | Telegram token хранится шифрованно в PlatformSetting и управляется royal через web UI. Глобальное включение также каноническое; runtime получает секрет по приватному API. | Готово. Токен не копируется в bot-файлы и не выводится в журнал. Реальный Telegram-вызов не выполнялся. |
| JSON-сопоставление парк → чат/тема | `Park.chat_id`, `Park.thread_id`, `bot_revision`; royal видит все парки, admin — назначенные. | Готово. Проверены 64-битный chat ID, optimistic revision и запрет thread без chat. |
| Редактор расписаний в Telegram-файлах | Native job CRUD доступен в web UI и зеркально в Telegram для royal/admin с тем же park scope. Типы: report, text, zoom, campaign. | Готово. Новые определения создаются выключенными. |
| Ежедневные/будние расписания, чередование A/B и пользовательские задания | `daily`/`hourly`, weekdays, `alternate=all/odd/even`, `anchor_date`, timezone и окна отчётов хранятся в PostgreSQL. | Готово; узкие scheduling-тесты прошли. |
| Разовая рассылка | `schedule=once` с timezone-aware `run_at`. | Готово; разовые задания не используют поля повторяющегося расписания. |
| Ручной запуск | Идемпотентный `/jobs/{id}/run` с request UUID и revision создаёт отдельную delivery, не меняя расписание. | Готово; включение выключенного задания требует явного `allow_disabled`. |
| Перенос locations/schedules/broadcasts/sk_campaigns | Royal preview/apply читает оба исторических data-root, сопоставляет только точный `Park.tag`, показывает конфликты, связывает review с состоянием БД и хранит source receipt/tombstone. | Готово на уровне API и fixtures. Все импортированные jobs выключены; исходные JSON и старые whitelist остаются без изменений. Преднастроенный импорт проверен на сохранившейся конфигурации отдельного бота: 11 парков, 11 адресатов, 74 выключенных задания. Полной копии хоста нет. |
| Старый scheduler и отметки отправки в файлах | Серверные slots и durable delivery: claim → content → begin → finish, lease, уникальность job/slot, terminal states и `unknown` после неоднозначной отправки. | Готово; проверены дедупликация, pause между claim/begin, истечение и конкурентный claim. Общий PostgreSQL gate: 31 passed. |
| Поиск робота и активные задачи | Приватный gateway повторно проверяет linked user, active/approved, Tracker read/write permission, назначенные парки, точный номер робота и park tag. | Готово; произвольное поле `rover` само по себе доступ не расширяет. |
| История ремонта и дополнительные представления | Команды `history`, `moves`, `moves_history`, `parts`; дополнительные очереди включаются royal через разрешённый allowlist. | Готово на уровне API/bot tests. Для auxiliary queue без разрешения возвращается явная ошибка, а не пустой результат. |
| QR YASADR | `native.qr.yasadr_code()` и `render_robot_qr()` восстанавливают старый payload и PNG. | Команда подключена; доступ требует видимой задачи робота в разрешённом парке. Проверено, что `а1460` передаёт в QR encoder строку `YASADR00000001460`. |
| Почасовой PNG отчёт | Matplotlib table renderer сохраняет группы статусов, колонки задачи/статуса/часов ремонта/простоя, старые пороги цветов, log-dump и легенду. До 40 строк на страницу. | Готово. Неизвестная подтверждённая история отображается как `—`, а не как выдуманный ноль. Все страницы отправляются в одной delivery-последовательности. |
| Watchdog превышения времени | HTML-safe `watchdog_parts()` использует старые пороги 3/5 часов и порядок log dump → red → yellow → green. Сообщения делятся по Telegram-лимиту. | Готово. Фраза «Задачи с превышением времени в очереди — отсутствуют» используется только когда это действительно следует из данных; неизвестная история отмечается отдельно. |
| KillSwitch/СК PNG | Отдельный 1080×1080 Pillow donut: зелёный `#22c55e`, остаток `#dbeafe`, процент округляется вверх, выполненной считается только задача со `status.key == closed`. | Готово. При усечённой выборке renderer пишет «среди видимых» и не утверждает общий 100%. |
| Статистика запросов и пауза | Счётчики today/month/total хранятся сервером. Royal может поставить паузу пользовательским запросам; delivery pause остаётся отдельным контролем. | Реализовано root+worker, bot/API focused tests прошли. Admin не может менять глобальную паузу. |
| Состояние процесса | Runtime публикует Telegram/scheduler health; web показывает ready/degraded/offline/unknown с server timestamp. | Реализовано. Stale heartbeat не считается успешным состоянием. |

## Визуальная проверка

Синтетический preview без реальных задач и идентификаторов сохранён в
`/private/tmp/native-report-parity-preview.png`. Он заново построен текущим Matplotlib-путём
`native.reports.render_report()` на 13 синтетических задачах; размер PNG 188354 байта. Он включает:

- все основные группы статусов от ожидания поставки до очереди;
- зелёные, жёлтые и красные интервалы ремонта;
- простои до 187 часов, отдельный log-dump и неизвестное время;
- исходный заголовок с числом активных задач и обе легенды.

QR проверен через spy над реальным `qrcode.QRCode`: вызов
`render_robot_qr("а1460")` передал в `add_data()` ровно
`YASADR00000001460` и создал чёрно-белый PNG 230×230.

## Выполненные проверки

```text
PYTHONDONTWRITEBYTECODE=1 \
UV_CACHE_DIR=/private/tmp/robopark-uv-cache \
MPLCONFIGDIR=/private/tmp/robopark-mpl-cache \
XDG_CACHE_HOME=/private/tmp/robopark-xdg-cache \
./scripts/verify.sh bot

75 passed, 14 third-party Matplotlib/pyparsing deprecation warnings
```

Образ `robopark-bot:parity` собран из `apps/bot/Dockerfile`. В контейнере с
`--network none --read-only --user 10001:10001 --cap-drop ALL` и
`no-new-privileges` успешно созданы табличный PNG, KillSwitch PNG и QR PNG:

```text
render-smoke-ok 62398 35902 394
```

```text
apps/api/.venv/bin/pytest -q \
  apps/api/tests/test_native_telegram.py \
  apps/api/tests/test_native_telegram_reports.py \
  apps/api/tests/test_native_telegram_scheduling.py \
  apps/api/tests/test_native_telegram_usage.py

55 passed, 2 framework deprecation warnings
```

Ранее отдельно выполнены PostgreSQL race-тесты optimistic job update, delivery
claim и одноразовой link-привязки: `3 passed`. Этот документ не заменяет
финальный общий suite, web build, container build и host smoke-test. Реальная
отправка Telegram, чтение Tracker и применение миграции на хосте здесь не
выполнялись.

## Проверки выпуска rc.36

- API: 120 focused tests пройдены после финальных изменений; 7 отдельных
  preset-тестов и фактический импорт зашифрованного набора также пройдены.
- Предыдущий полный API-прогон: 2958 passed, 35 skipped, 1 failed. Причина
  единственного сбоя — оставленный между тестами cache истории Tracker.
  Фикстура исправлена; связанный набор из 77 тестов прошёл. Полный API-прогон
  после исправления заново не выполнялся.
- PostgreSQL 17: 31 passed, включая конкурентное одобрение доступа и delivery.
  Повторный прогон использовал временный tmpfs для тестовой БД: локальный
  Docker-диск был заполнен. Рабочие данные и чужие volumes не очищались.
- Host/OTA: 1339 passed; дополнительные тесты проверяют наличие ciphertext
  в реальном архиве и отказ неверного ключа до подготовки NVMe (156 focused tests passed).
- Fast gate: 79 passed, web lint и 33 navigation routes.
- Web: 202 файла, 2933 теста пройдены. Web build и Docker API/web/bot успешно собраны. Chromium E2E: 1 passed
  (desktop-настройка чата/расписания и mobile-привязка Telegram).
- npm audit и pip-audit для API и bot: известных уязвимостей не найдено.
- Поиск реальных токенов и ключа preset по отслеживаемым исходникам: 0 совпадений.

Преднастроенная установка проверена локальными тестами и импортом конфигурации,
но не полным запуском на физическом Khadas. Сетевой доступ Tracker/Emergency,
валидность перенесённых токенов, внешний SSH и доставка в реальные чаты
потребуют проверки после включения хоста. Восьмичасовой тест не проводился.
