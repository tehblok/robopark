# Telegram как штатный сервис Robopark

## Требования пользователя

Бот — встроенная функция Robopark: единые парки и точные Tracker-теги,
настройки чата/темы на парк, вкладка управления для royal и администраторов,
отчёты, текстовые рассылки, Zoom и расписания. Поиск задач и история ремонтов
для механиков и управление через Telegram сохраняются. Royal управляет токеном
и глобальным включением; администратор — только назначенными ему парками.

## Выбранное устройство

API и PostgreSQL владеют конфигурацией, привязками Telegram, расписаниями и
журналом доставок. Отдельный процесс `bot` — штатный транспорт Telegram и
исполнитель подготовленных отправок. Его сбой не блокирует веб/API. Существующий
контракт host lifecycle, шифрование токена, резервные копии и OTA сохраняются.
Простая синхронизация двух JSON-каталогов не решает расхождение прав и парков;
запуск polling внутри API мешает независимому перезапуску и создаёт конкурирующих
получателей updates. Поэтому используется один управляемый bot-процесс с API.

Исходники `tracker-report-server` используются для сверки исправлений и поведения.
Его credentials, встроенные Telegram ID, отдельные установщик/OTA/heal и данные
не копируются. Старые данные не удаляются и не активируются автоматически.

## Данные и права

- `Park.id/name/tag/timezone/is_active` — единственная идентичность парка.
- Настройки Telegram принадлежат `park_id`: chat_id, thread_id, revision.
  Chat ID — 64-битный целочисленный идентификатор; пустой чат явно отключает
  доставку. Список парков из старых seeds никогда не восстанавливается.
- Задание принадлежит одному парку. Типы: report, text, zoom, campaign.
  Поля: title, enabled, schedule (daily/hourly/once), time, weekdays (0=Пн),
  start_hour/end_hour, text, url, tracker_tag, alternate (all/odd/even),
  anchor_date, timezone, run_at. Для повторяющихся заданий пустой timezone
  означает пояс парка; старые задания сохраняют исходный пояс. run_at — точный
  момент разовой отправки. Удаление реально
  удаляет определение; нет автоматического восстановления штатных рассылок.
- Telegram ID связывается с существующим активным пользователем
  посредством одноразового кода из его веб-сеанса (10 минут, hash в БД).
  Код погашается командой `/link CODE` только в личном чате бота. Изменение
  ролей/доступа в Robopark действует и в боте. Username Telegram не доказывает
  идентичность. Старые неподтверждённые whitelist/admin IDs не дают новых прав.
- Pending mechanic/operator может связать Telegram и подать ParkRequest.
  Доступ к задачам появляется после одобрения. Сайт и бот используют один
  сервис решений с row lock, revision и аудитом; у парка несколько admin.
  Rejected пользователь не может самостоятельно восстановить глобальный доступ.
- Royal видит все парки, admin — только UserPark. Механик/оператор читает задачи
  своих парков. Все публичные и внутренние операции повторяют проверку прав
  на сервере; Telegram-кнопки сами по себе не являются проверкой доступа.

## API-контракт

Все публичные пути ниже требуют текущего пользователя, кроме приватных вызовов.
Приватные пути требуют существующий X-Robopark-Bot-Key.

- GET `/admin/bot/native` -> `{parks: ParkBot[], jobs: BotJob[], deliveries: Delivery[], health}`.
  `ParkBot`: id, name, tag, timezone, chat_id, thread_id, revision.
  `BotJob`: id, park_id, kind, title, enabled, schedule, time, weekdays,
  start_hour, end_hour, text, url, tracker_tag, alternate, anchor_date, revision.
  `Delivery`: id, job_id, park_id, title, state, scheduled_at, finished_at,
  error_code (без текста сообщений и секретов).
- PUT `/admin/bot/native/parks/{id}` body `{chat_id,thread_id,revision}` -> ParkBot.
- POST `/admin/bot/native/jobs` body BotJob без id/revision -> BotJob.
- PUT `/admin/bot/native/jobs/{id}` body BotJob с revision -> BotJob.
- DELETE `/admin/bot/native/jobs/{id}?revision=N` -> 204.
- POST `/admin/bot/native/jobs/{id}/run` body `{revision,request_id,allow_disabled}`
  создаёт ручную доставку с защитой от повтора по UUID, не меняя расписание.
- GET `/access`, POST `/access/requests` — статус и запрос парка текущего
  пользователя. Внутренние зеркала привязывают пользователя к Telegram ID.
  GET `/internal/bot/native/access/requests?telegram_user_id=N` возвращает
  заявки парков администратора; POST `.../{id}/decision` с `{approve,revision}`
  атомарно принимает решение.
- GET `/bot/account` -> `{linked:bool,telegram_user_id:int|null}`.
- POST `/bot/account/link-code` -> `{code,expires_at}`; старый непогашенный код
  этого пользователя аннулируется; секрет не журналируется.
- DELETE `/bot/account` -> 204; отзывает только собственную привязку.
- POST `/internal/bot/native/link` body `{code,telegram_user_id}` -> `{linked:true}`.
- GET `/internal/bot/native/context?telegram_user_id=N` ->
  `{user_id,role,name,parks:ParkBot[],can_manage:bool}`; неизвестный ID -> 403.
- GET `/internal/bot/native/robots/{robot}?telegram_user_id=N&view=open|history|moves|moves_history|parts`
  -> `{robot,issues:[...],truncated:bool}`. Нормализация номера, exact matching,
  ограничения парков и очередей выполняются сервером; история — закрытые
  repair/service/calibration с resolution=fixed.
  Дополнительные очереди требуют явного разрешения royal и доказанного робота
  в назначенном парке; используются rover и поддерживаемые форматы заголовков.
- Управление из Telegram использует те же native CRUD через приватные зеркала
  `/internal/bot/native/manage/...` с telegram_user_id; сервер разрешает
  royal/admin и ограничивает парк точно как веб-запрос.
- POST `/internal/bot/native/claim` -> `{deliveries:[{id,lease_token,job,park}]}`.
  Время берётся сервером; ранее завершённый слот повторно не выдаётся.
- POST `/internal/bot/native/deliveries/{id}/content` body `{lease_token}` ->
  `{issues,truncated}`: ограниченная выборка для отчёта/кампании по текущему парку.
- POST `/internal/bot/native/deliveries/{id}/begin` body `{lease_token}`:
  повторно проверяет актуальность/доступность парка, задания и чата, возвращает
  `{ready:true}` только владельцу claim; disabled/deleted -> 409.
- POST `/internal/bot/native/deliveries/{id}/finish` body
  `{lease_token,state:sent|failed|unknown,error_code?:str}` -> `{recorded:true}`.
- POST `/internal/bot/native/health` -> 204; состояние Telegram/планировщика,
  серверное время и безопасный код ошибки. Старые сведения помечаются offline.
- GET `/admin/bot/native/migration/preview`, POST `/admin/bot/native/migration/apply`
  body `{fingerprint}`: перенос доступен только royal и сохраняет исходные файлы.

## Исполнение и наблюдаемость

Один polling-процесс удерживает постоянный flock. Offset сохраняется атомарно.
Чтение команд и плановые отправки не выполняются в одном блокирующем цикле.
Четыре ограниченные очереди обрабатывают пользователей параллельно; каждый
пользователь остаётся в одной последовательной очереди. Offset подтверждает
только завершённую часть принятого пакета, размер пакета не превышает 20 updates.
Внешние запросы имеют timeout, ограниченный размер и retry только там, где
безопасно. Ошибка одного задания не блокирует остальные.

Уникальность `(job_id, scheduled_at)` в БД предотвращает повторную выдачу слота.
Подготовка отчёта выполняется до `begin`. Перед внешней отправкой записывается
`sending`; потеря ответа после отправки означает `unknown`, без автоматической
повторной отправки. Статус доставки не заменяется heartbeat процесса. Вкладка
показывает последние sent/failed/unknown и понятную ошибку вместо вечной загрузки.
Просроченные более чем на 15 минут слоты не отправляются. Журнал ограничен
90 днями, экран показывает до 100 последних доставок каждого доступного парка.
Явный ответ Telegram 429 с retry_after до 30 секунд допускает один повтор;
сетевые ошибки и неизвестный результат отправки повторов не вызывают.

## Перенос и проверка

Старые конфигурационные файлы сохраняются как источник миграции. Перенос сначала
сверяет точные Tracker-теги с существующими парками, показывает конфликты и
непереносимые записи; включение отправок остаётся отдельным явным действием.
Новый runtime не запускает старый scheduler параллельно.

Проверки: RBAC чужого парка и Telegram ID, одноразовая привязка/отзыв, 64-битный
чат, optimistic revision, пустое расписание и удаление, серверное расписание и
дедупликация, отключение между claim и send, поиск/история по точному роботу,
ограничения API, Telegram callback flows с fake transport, ошибки UI и сборка.
Реальная отправка и проверка на Khadas требуют отдельного тестового получателя;
тесты не рассылают сообщения сотрудникам.
