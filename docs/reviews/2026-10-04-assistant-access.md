# Граница доступа локального помощника

Status poll помощника теперь сбрасывает защищённый workspace при `401`/`403`,
но прежнее состояние status могло пережить смену пользователя. Пока initial
status нового identity загружался, React успевал смонтировать chat/knowledge с
`ready` предыдущего пользователя. Если новый запрос завершался `403`, общий
loader сохранял старые data и панели оставались доступны.

`AssistantPage` теперь задаёт синхронную React `key`-границу по полному identity:
id, role, access status и отсортированным permissions. При изменении любого из
этих полей workspace пересоздаётся с пустым status до первого ответа нового
identity. Старые дочерние запросы и status poll отменяются cleanup-обработчиками.

Poll дополнительно проверяет флаг cleanup до API-вызова. Поэтому callback,
который browser уже поставил в очередь до `clearInterval`, после unmount или
смены identity не выполняет запоздалый `status()`.

Регрессии проверяют два контракта:

- `ready` пользователя A, смена на пользователя B и initial `403` не монтируют
  chat нового пользователя, не запускают дочерний read и не создают status poll;
- вручную выполненный старый interval callback после unmount не вызывает API.

Целевая проверка:

```text
PATH=/Users/tehblokdan/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/bin:$PATH npm test -- --run src/domains/assistant/AssistantPage.test.tsx
Test Files  1 passed (1)
Tests  40 passed (40)

PATH=/Users/tehblokdan/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/bin:$PATH npm run lint -- src/domains/assistant/AssistantPage.tsx src/domains/assistant/AssistantPage.test.tsx
oxlint: exit 0

PATH=/Users/tehblokdan/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/bin:$PATH npm run build
vite: 2365 modules transformed; build and asset compression completed
```

Docker/Playwright не запускался; браузерную проверку выполняет родительский
процесс после объединения остальных исправлений.

Combined acceptance: 2,857 frontend tests passed, full oxlint passed. The 21-case Linux browser regression run passed, including campaign create/edit at 320/390/1440px and assistant loading/error/empty/stale/denied/chat/knowledge states. Independent review found no concrete remaining issue in these changes.
