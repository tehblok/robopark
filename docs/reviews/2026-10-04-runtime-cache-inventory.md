# Additional API runtime-store inventory

Date: 2026-10-04. Reviewed against next-slice source 171344bc.

A targeted read-only review checked module dictionaries/sets, cache registries
and lock bookkeeping beyond the already repaired response/Tracker caches,
metrics cache, Tracker client lifetime, live merge and media uploads.

No additional high-confidence unbounded production store was found in this
scope:

- database_locks process lock entries are removed after their last user exits.
  The PostgreSQL lock engine map is keyed by configured database URL and has an
  explicit disposal path; request IDs do not create engines.
- cache_cleanup attachment scan cursors are keyed by a fixed cleanup owner and
  database URL and removed when the scan reaches its end.
- operational_health keeps one snapshot-cache entry and a deque of at most
  12 RSS samples.
- cache_metrics family names come from fixed production namespaces in the
  inspected call sites, rather than user-supplied per-request identifiers.

This is a bounded source inspection, not a memory soak or a proof that every
runtime allocation is bounded. In particular, adding dynamic metric-family names
would require a new cardinality review. No runtime change was made solely for
these observations.
