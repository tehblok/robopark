# Ограничение terminal history

Завершённые UUID текущего broker epoch намеренно оставались в памяти: повтор
того же create возвращал завершённый статус и не запускал новый root или
maintenance worker. Из-за этого очистка дисковой истории не освобождала
`TerminalRegistry`, а жёсткий предел 4096 записей навсегда блокировал новые
сессии до перезапуска broker.

Теперь периодический maintenance tick при 3072 записях и пустом live set
сначала меняет epoch/revision и атомарно публикует capability fence. Только
после успешной публикации он сокращает registry до 2048 последних завершённых
статусов и причин. Порядок retention задаёт первое завершение сессии, а не её
admission; идемпотентный повтор finish не делает старый статус новым. В
сокращение входят tombstone, которые остались только в памяти после ошибки
записи журнала. После безопасной idle-ротации или перезапуска дисковая история
сокращается до 2048 записей с прежним пределом 30 дней. Descriptor прежнего
epoch отклоняется до вызова runtime, поэтому удалённый UUID не получает новый
TTL.

Живая maintenance или root-сессия откладывает ротацию, потому что API attach
привязан к единому epoch. До её завершения дисковая история и registry могут
вырасти до жёсткого fail-closed предела 4096. Ошибка публикации возвращает
прежние epoch/revision и не удаляет
tombstone; ёмкость не освобождается до успешной публикации новой границы.
После успешного сокращения повторный tick не меняет fence без нового накопления
истории. Уже выданное, но ещё не использованное подтверждение TOTP со старой
revision потребует повторной авторизации.

Это восстановление ёмкости при ограниченном churn, а не разрешение
неограниченного роста. В памяти остаётся до 2048 reconciliation-статусов;
новая ротация происходит после следующего накопления до 3072. Регрессии
проверяют threshold, replay старого root descriptor без запуска runtime,
сохранение live-сессии и fail-closed предела, ошибку публикации, отсутствие
повторной ротации и приём свежего descriptor после fence.

## Verification

The initial new regressions failed before the implementation. Independent review
then found admission-order retention could discard a recently ended long-lived
session; its regression failed before the completion-order fix. The final host
terminal subset passed 99 tests, including replay, live-session deferral, failed
publication and idempotent completion order. Ruff lint/format and diff checks
passed. Follow-up independent review found no remaining concrete issue in this
scope. No live host or root terminal acceptance was run.
