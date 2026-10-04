# SQLite schedule time preservation

A real model/service reproduction shows that SQLite strips timezone offsets from
ScheduleEntry boundaries. A 2026-11-01 09:00 New York shift is later interpreted
as 09:00 Moscow; routing can activate it before the actual start. Production
installation requires PostgreSQL, but offline SQLite remains supported.

Use a ScheduleEntry-only TypeDecorator over the existing timezone-aware DateTime.
For SQLite, normalize aware writes and comparison parameters to Moscow wall time,
preserve naive historical input, and attach Moscow timezone on reads. Keep
PostgreSQL timestamptz behavior and the schema unchanged. Do not rewrite stored
rows; offsets already lost from historical non-Moscow writes are unrecoverable.

1. Add failing persisted API read/list, strict routing-boundary, and historical
   raw-row tests, including cross-date/non-Moscow values.
2. Implement the narrow model type; check filtering/overlap/pagination/retention.
3. Run schedules and migration comparison tests, then actual PostgreSQL
   persistence with non-Moscow input. Review behavior and record limitations.

The SQLAlchemy extension preserves dialect processing and applies to comparison
parameters: [official TypeDecorator documentation](https://docs.sqlalchemy.org/en/20/core/custom_types.html#sqlalchemy.types.TypeDecorator).
This work is isolated from the rc.28 candidate currently in CI. No eight-hour test.
