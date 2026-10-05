# SQLite schedule boundaries

The existing timezone-aware SQLAlchemy DateTime did not preserve offsets in
SQLite. A New York 09:00 shift on 2026-11-01 was persisted as bare 09:00 and later
presented as Moscow 09:00 (06:00Z instead of 14:00Z). UTC query bounds were also
compared with local wall strings, affecting even Moscow schedules. Production
installation uses PostgreSQL; this fix concerns the offline SQLite path.

A ScheduleEntry-only type normalizes SQLite writes and query binds to Moscow
wall time and attaches Moscow on reads. Existing naive rows keep their established
meaning and bytes. PostgreSQL bypasses both conversions and retains timestamptz.
No schema or data migration is needed; the migration/model comparison passed.

Five new cases failed before the fix and then passed: persisted New York,
Auckland and Moscow patterns, exact operator shift boundaries, and a raw legacy
row. The full schedule suite passed (57); model/migration plus real PostgreSQL
workflow tests passed (48), including non-Moscow schedule input and delivery to
an employee currently on shift. Offline sync, push, notification and admin-user
callers passed (106). Ruff, module boundaries and diff checks passed. Independent
read-only caller audit found no new blocking concern.

Historical non-Moscow offsets already discarded by SQLite cannot be recovered.
Raw SQL imports bypass the type and must continue using Moscow wall strings.
Production PostgreSQL import from old SQLite remains unsupported. This branch
has not been installed on the live host; no eight-hour test was run.
