# Приёмка интерфейса А — 0.1.43

Дата: 2026-09-19. Ветка: codex/product-completion. База: cea51be.
Новый А включается через «Ещё → Интерфейс». По умолчанию остаётся классический.
Один владелец данных, форм и фото; переключение не создаёт второй API-клиент.
На рабочий хост ничего не установлено, реальные Tracker-тикеты не изменялись.

## Проверено локально

- Web: 145 файлов, 2063 теста пройдены после исправлений ревью; production build пройден.
- Два дизайна × пять ролей × пять ширин: 803 разрешённых сценария. Первый прогон: 787 passed, 16 failed, 250 недоступных сочетаний skipped. Причина 16 — min-content растягивал форму создания репорта в А; исправлено, повторные 50 сценариев создания репорта прошли. Skipped не считаются проверенными отказами.
- Operational: первый полный прогон 341 сценария — 328 passed, 13 failed. Старые фикстуры не соответствовали workflow, timeline, автоматическим ракурсам и текущим названиям. Повторный прогон изменённых файлов и новых запретов: 21 passed, затем последние 5 сценариев навигации passed.
- Дополнительно 60 снимков/проверок А: 15 основных экранов × светлая/тёмная тема × 390/1440. Все passed. Проверены overflow, читаемость, области нажатия и axe. Репрезентативные снимки просмотрены вручную; это не означает ручного осмотра каждого пикселя всех 60.
- 50 циклов переключения А/классика и вкладок ремонта сохраняют File и текст, не увеличивают число interval и не отправляют записи. Это не heap/GC soak-test.
- Проверки камеры включают остановку fake MediaStreamTrack; физическая камера OnePlus не проверена.
- SW: 2 сценария passed, включая смену версии по байтам, offline HTML, отсутствие кэширования API/приватных вложений, отсутствие принудительного skipWaiting. Unit-проверки регистрации PWA и fallback классики при отказе CSS входят в web suite.
- OTA/метаданные/восстановление: 60 host-тестов passed, границы Docker/systemd имитируются. Это не установка на ARM.
- Упаковка: 126 тестов passed. Удалены dev demo/load helpers из payload; найден и синхронизирован устаревший APP_VERSION в ops (0.1.40 → 0.1.43). Штатная проверка совместимости metadata 0.1.42 → 0.1.43 пройдена.
- Lint: exit 0, 21 предупреждение (refs/dependencies и существующий код), не «без предупреждений». Contrast-script passed.

## Вложенные сценарии (не заменены счётчиком маршрутов)

Работа: взятие, передача, комментарий, один снимок и код дефекта, операторская проверка/возврат; изолированный настоящий API.
Роботы: источник snapshot один, автоматический ракурс, маркеры, 403, разметка, неизвестные ошибки, предпросмотр, ошибка фото.
Склад: поиск, компоненты, поставки, инвентаризация, печать/выгрузка, ограничения оператора, повторное нажатие/409.
Репорты: сохранение фото, отказ и повтор, необратимое удаление с подтверждением, права админа/royal.
Кампании: создание, отдельные настройки, комментарий/фото, фильтр робота, старые ответы и 403.
Управление: права/черновики, настройки, статусы хоста, защита незавершённой операции обновления.
Обзор/аналитика: все роли, сравнение, фильтры, переход к деталям, выбранный парк.

## Независимое ревью

Critical: нет. Три Important воспроизведены тестами RED→GREEN:
1. При 401/403 склад очищает строки, местоположение, выбранные/подготовленные этикетки; поздние ответы компонентов отсечены.
2. Тип существующей кампании неизменяем в редакторе: серверный PATCH его не поддерживает.
3. Вкладки доступны с клавиатуры, даже если открыт связанный список задач вне основного tablist.
Также устранены переполнение формы репорта и ARIA-ссылки маркеров на отсутствующие детали.
Отдельных Minor от ревьюера нет.

## Нагрузка: границы результата

Изолированные 200 cookie-сессий, SQLite, два Uvicorn worker, локальный имитатор интеграций (100 ms задержки), без production/Wi-Fi/Tuna.
20 секунд, 3 секунды между действиями клиента: 1334 запроса, 66.69 req/s, p50 16.63 ms, p95 189.71 ms, p99 299.25 ms, 0 неожиданных ответов/5xx/таймаутов; сумма peak RSS worker 365.53 MB.
При холодном одновременном входе p95 2318.62 ms, холодные детали 1581.30 ms. Два контрольных пункта coalescing не прошли: search/detail вызваны дважды на два worker вместо ожидаемого единственного общего вызова. Остальные проверки harness true.
Скрипт interface-load.mjs отдельно проверяет один общий backend-контракт под метками двух дизайнов, без рендеринга браузера; это не сравнение двух разных серверов.
CPU usage отдельно не измерен; host CPU count есть в сыром отчёте. Долгий heap/GC soak и целевые 200+ пользователей по Wi-Fi не проверены. Не заявляем готовность хоста к любой нагрузке.

## Решения и отклонения

- Ruling: translate task headings to tool-compatible Task N without changing scope — task-start parser requires this — no product impact.
- Task 1: Ruling: put InterfaceModeProvider directly inside AuthProvider instead of main — account lifecycle stays co-located, provider remains above routes — cost if wrong: extra provider in AuthProvider unit renders, covered by suite.
- Task 1: Ruling: track fetchWithTimeout including body consumption — all JSON, inventory, diagnostic and form writes converge here, no retry loop exists at this transport — cost if wrong: multi-request workflows need an additional transaction guard; assess during workflow stages.
- Task 2: Ruling: preserve CatchAll safe landing redirect rather than inventing a new 404 flow — current router deliberately redirects, temporary loading element is not the final route — cost if wrong: user sees landing screen instead of explicit missing-page notice.
- Task 3: Ruling: preserve one mounted task panel and present chat via a visibility attribute, rather than moving the comment/photo component between parents — preserves live File and drafts, no second UI/data tree — cost if wrong: hidden chat markup stays in DOM until task unmount.
- Task 3: Ruling: existing robots E2E expected royal read-only, contradicting agreed rights and existing implementation — replace with actual edit assertions for both admin and royal, no permissions code change — cost if wrong: undesired permissions would need explicit policy clarification.
- Task 3: Ruling: raise prototype's 12px diagnostic captions to 14px after responsive contract failed — preserve composition but meet existing readability floor — cost if wrong: slightly taller classic card. Embedded classic check remains stacked; full-page split must not squeeze the task pane.
- Task 3: Ruling: responsive golden screenshots predate approved 5b465cc baseline (e.g. missing My Tasks nav and old gutters), and 899px filter assertion expects 2 columns while 5b465cc CSS has 4 — refresh goldens to accepted baseline, replace private column-count assertion with usable status filter + existing overflow contract — cost if wrong: visual baselines could mask unrelated changes; inspect representative images and retain functional/a11y assertions.
- Task 3: final responsive suite 63 passed; interface-work 5 passed including denied data; build passed. Ruling: preserve functioning /overview and test legacy redirect at /dashboard — route behavior predates redesign — cost if wrong: old consumers expecting overview redirect must use dashboard/work.
- Task 4: Ruling: operator readonly overrides stale permissive UI permissions, matching explicit user restriction; legacy operator-management tests moved to mechanic and replaced by real operator readonly assertions — cost if wrong: individually elevated operator must use another role to manage stock; server remains authority.
- Task 4: Ruling: pending document confirmation intentionally blocks the interface menu; test duplicate click while pending and switch after settlement rather than bypassing modal — preserves focus safety; general pending-switch store verified in tasks 1/3 — cost if wrong: user must await request before choosing design.
- Task 4: Ruling: raise A shell captions to 14px after existing responsive contract failed — legibility wins over compact labels — cost if wrong: bottom labels can wrap to two lines.
- Task 5: Ruling: add missing campaign rule editor through existing PATCH endpoint and protect responses by owner/generation — spec requires editing rules separately, no alternate API — cost if wrong: managers now see an extra collapsible form in classic too.
- Task 5: Ruling: fix legacy .form-grid hardcoded 10px radius to shared token after six admin visual contracts failed — structural design token consistency, no behavior change — cost if wrong: classic legacy fields become 2px less rounded.
- Task 7: Ruling: update legacy diagnostic tests for automatic views, WebP photos, cleaned-body label and royal edit rights — these are existing user-approved behavior, not removed coverage — cost if wrong: policy drift; real API tests verify admin/royal writes and operator/custom-role denial.
- Итоговое независимое ревью начато параллельно заключительным тестам для экономии времени; рассмотрены все production-изменения и исправлены все Important, без второго ревью.
- Старые E2E приведены к уже согласованным действиям вместо восстановления ручной смены статуса/старых кнопок; цена ошибки — неверная фикстура, поэтому действующий полный цикл дополнительно проверен настоящим изолированным API.
- Нагрузку выполняет существующий изолированный harness, новый wrapper не принимает внешние адреса. Цена: нельзя объявить прогон тестом реального Wi-Fi-хоста.
- Диагностические артефакты и ledger сохранены до целевой приёмки: ограничения нагрузки/физического устройства открыты, полная эксплуатационная приёмка не объявлена.

## Обновление

Используется штатный pack-release.sh, существующий ключ (публичная часть совпадает с deploy/keys/release-public-key.pem), схема БД 0032_operator_inv_readonly. Миграция отзывает у системной роли оператора три складских write-права; политика допуска OTA не менялась.
Архив следует передавать по доверенному каналу. Проверка ARM/Docker/systemd/Tuna и установленной PWA при переходе с фактической предыдущей сборки остаётся проверкой на целевом устройстве.
