# Черновик формы кампании при переходе на detail

CI run `37215819846`, shard 1, обнаружил race после успешного создания
кампании. `history.pushState` уже менял URL на `/campaigns/4`, а React Router
ещё оставлял в DOM create-форму. Playwright дождался нового URL, ввёл
`смена подвески` в старую форму, после чего переход завершился и detail-форма
смонтировалась с серверным `замена колёс`. Поэтому ввод выглядел как сброшенный
ответом сервера, хотя запрос обновления кампании ещё не выполнялся.

После успешного `createCampaign` create-форма теперь синхронно заменяется
доступным loading-состоянием до вызова навигации. Между обновлением browser URL
и commit нового маршрута больше нет редактируемого устаревшего поля. Edit-форма
существующей кампании не меняется: после `updateCampaign` она остаётся доступной.

Unit-регрессия использует настоящий `BrowserRouter`, перехватывает точный момент
`pushState` и задерживает GET detail. До исправления при уже новом URL тест видел
старый textbox со значением `замена колёс`; после исправления textbox отсутствует,
а пользователь видит статус `Открываем кампанию`.

Проверка:

```text
PATH=/Users/tehblokdan/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/bin:$PATH npm test -- --run src/domains/campaigns/CampaignsPage.test.tsx
Test Files  1 passed (1)
Tests  31 passed (31)

PATH=/Users/tehblokdan/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/bin:$PATH npm test
Test Files  194 passed (194)
Tests  2852 passed (2852)

PATH=/Users/tehblokdan/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/bin:$PATH npm run build
vite: 2365 modules transformed; build and asset compression completed
```

Docker/Playwright не запускался: исправление проверено детерминированным unit-тестом,
а браузерная VM в этой проверке зарезервирована родительским процессом.

Combined acceptance: 2,857 frontend tests passed, full oxlint passed. The 21-case Linux browser regression run passed, including campaign create/edit at 320/390/1440px and assistant loading/error/empty/stale/denied/chat/knowledge states. Independent review found no concrete remaining issue in these changes.
