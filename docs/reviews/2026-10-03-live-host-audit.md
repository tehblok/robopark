# Live-аудит хоста 03.10.2026

Исходный релиз при проверке терминала: `0.2.0-rc.20.dev11411368079194000089`, build `7bf818605ab8996f`.
OTA `70698b8c-3763-4500-941e-59fb4795d8bb` завершена; rollback указывает на rc.19.
Проверки выполнялись через браузерный терминал с отдельными TOTP владельца.
Обе проверочные сессии завершены штатно, причина `closed`.

## Подтверждено на хосте

- Обычный терминал: WebSocket 101, UID997/GID988, только собственная группа,
  CapEff=0, NoNewPrivs=1. Кириллица, чтение/запись TemporaryFile в HOME,
  Ctrl+C и закрытие работают. `/etc/shadow` и API broker socket недоступны.
- Root: UID0, CapEff=`000001ffffffffff`; setup, broker, основной сервис и
  commands.path активны. Все четыре контейнера Robopark healthy.
- API/worker: UID10001, CapDrop=ALL, no-new-privileges. Checkout-wide `/host-repo`
  отсутствует; compatibility env и терминальный socket смонтированы read-only.
  Текущий release — root:root 0700; приватные state/config — root:root 0700.
- Web8080 и PostgreSQL5432 слушают loopback. API8000 наружу не опубликован.
- Предупреждения retention связаны с busy host operation/терминалом; выбранный
  BuildKit сообщает blocked=false. Старый diagnostic snapshot не выдаётся за свежий.

## Сетевой ADB закрыт по указанию владельца

`usb-gadget-khadas.service` запускал root adbd с TCP5555 на всех интерфейсах.
Владелец подтвердил, что сетевой ADB не использует. Установлены:

- `/etc/nftables.d/robopark-adb-guard.nft`, root:root0600;
- `/etc/systemd/system/robopark-adb-guard.service`, root:root0644, enabled/active.

Отдельная таблица `inet robopark_adb_guard` отклоняет TCP5555 для IPv4/IPv6,
не очищая существующие таблицы Docker. `nft --check` и systemd verify прошли.
Connect к127.0.0.1:5555 и[::1]:5555 возвращает ECONNREFUSED, счётчик правила2.
SSH22 доступен, публичный сайт отвечает200, USB gadget остаётся active.
Проверка после реальной перезагрузки ещё не проводилась. Механизм описан в
[документации nftables](https://wiki.nftables.org/wiki-nftables/index.php/Configuring_chains).

Откат этой отдельной настройки: `sudo systemctl disable --now robopark-adb-guard.service`.
Он удалит только созданную таблицу и снова откроет TCP5555, поэтому выполняется
только при явной необходимости сетевого ADB. Настройка находится вне immutable
релиза Robopark и сохраняется при его OTA/rollback.

## Старые экземпляры BuildKit

Текущий durable receipt и приватный buildx registry выбирают
`robopark-buildkit-4184e014faa24e108f9e7e9286ca8867` (порядка1.8GiB).
Три более ранних экземпляра с собственными корректными owner markers отсутствуют
в действующем registry и содержали только процессы docker-init/buildkitd:

- `robopark-buildkit-1e135e09571441eca194ed2857fabb53`;
- `robopark-buildkit-b426bd1b604a47deab8524345b3a52ea`;
- `robopark-buildkit-aeef42684c444e4e907f5195d8370669`.

Они остановлены, restart policy=no; текущий builder и четыре контейнера продолжают
работать. Их cache volumes (примерно по1.3GiB) сохранены: свободное место этим
действием не увеличено. Безвозвратное удаление не выполнялось. Обратное действие —
запуск нужного exact container; прежнюю restart policy при необходимости задают явно.

## Найденный дефект адреса клиента

Два одиночных публичных запроса `/api/auth/me` (обычный и с подставленным XFF)
вернули401. Tuna заменил подставленный заголовок одной и той же цепочкой
`<external-client>, 127.0.0.1, 127.0.0.1`, однако nginx с `real_ip_recursive off`
зарегистрировал remote_addr127.0.0.1. Это объединяет IP-based лимиты разных клиентов.

Минимальный кандидат меняет только `real_ip_recursive on`; точный список доверенных
peer остаётся прежним. Настоящий Docker/nginx regression: RED на off → GREEN на on;
недоверенный sibling со spoofed XFF остаётся своим IP. Независимое ревью — без findings.
После установки rc.21 публичный браузерный запрос подтверждён карточкой активности
владельца: API сохраняет внешний IP вместо127.0.0.1. Повторная публичная подстановка
XFF после OTA не выполнена: браузерный CDP не поддержал изменение заголовков;
состояние браузера этим вызовом не изменено. Локальная проверка недоверенного
источника выше и прежняя проверка нормализации Tuna остаются отдельными свидетельствами.

## Остальные наблюдения

`camera_isp_3a_server.service` failed; четыре сервиса приложения healthy. Камера
самой платы не проверена и служба не изменена. Два legacy таймера tracker содержат
игнорируемый systemd ключ `Timezone=Europe/Moscow`; сам хост уже использует MSK,
поэтому наблюдаемые ближайшие запуски соответствуют MSK. Их назначение и связь
с прежним Telegram-ботом нужно подтвердить до изменения/отключения.

Evidence: `output/audit/2026-10-03/continuing-audit/live-root-host-audit.txt`,
`.jpg`, `real-ip/`; ordinary proof — `royal-terminal/feature-v12/live-maintenance-proof.*`.
Полный API-прогон: 2656 passed, 5 failed, 22 skipped, 1 deselected (без load).
Все пять падений воспроизведены адресно: устаревшие ожидания количества nginx
Permissions-Policy, списка режимов verify.sh, terminal-таблиц и head0055 вместо0056.
Ожидания обновлены с отдельной проверкой строгой политики терминала, таблиц,
индексов, CASCADE-связей и totp_only. Повторный запуск двух модулей:57 passed;
Ruff и форматирование passed. Первичный полный прогон не обозначается полностью
успешным и целиком повторно не запускался после этих изменений только в тестах.
Восьмичасовой тест не проводился.

## Установленный rc.21

`0.2.0-rc.21.dev13181400260723547202`, inner SHA256
`eb2168458af4a6588c49f0f7100931bd690845bfff58ac5f69b2668014002092`,
outer SHA256 `5d2293cd875fae78c4c8b2d549b3f8e93e0d9636745baa64063f87e5b9407dbf`.
Совместимость ограничена точным установленным rc.20. Сравнение архивов: единственная
правка runtime — real_ip_recursive on; прочее — версии, совместимость и описание
обновления. Формат OTA1, все выпущенные миграции и зависимости неизменны.
Проверка архива passed; тесты сборщика/совместимости28 passed. Предыдущая сборка
в feature-v13 заменена до загрузки из-за устаревшего описания релиза.
Итоговые артефакты: output/audit/2026-10-03/continuing-audit/feature-v13-final/.
Владелец подтвердил установку свежим TOTP. Операция
`8931d44f-2151-4a40-8ab3-abd6e69a0f48` завершена; админ-панель подтверждает версию
rc.21 и build `eb2168458af4a658`. Rollback указывает на точный прежний rc.20.
Страница «Система» показывает PostgreSQL, контейнеры, Worker, Tracker и Tuna
работающими, очистку без ошибок и проверенную резервную копию. Отдельный полный
doctor snapshot помечен интерфейсом устаревшим (7 минут) и не выдаётся за новый.
Терминальная страница доступна, оба профиля предлагаются, активных сессий нет;
повторная PTY-сессия после OTA не открывалась. Ошибок console при навигации нет.
Публичная проверка IP выполнена через собственную карточку активности владельца,
без нового root-доступа. Evidence: live-system-after.txt, live-version-after.txt,
live-client-ip.txt, live-rc21-installed.jpg в feature-v13-final/.
