# Bounded operator report cache

The final operator report cache retained expired reports until that exact user/park key was requested again. Unlike the underlying Tracker query caches, it had no capacity limit. Changed park configurations and departed users could therefore leave payloads resident for the lifetime of the API worker.

The in-process cache now keeps at most 128 reports and 4 MiB of encoded key/payload bytes, evicts least recently read entries, and sweeps expired entries on both reads and writes. Oversized reports are returned normally by the route but are not retained. A single lock covers dictionary, LRU and byte accounting. JSON snapshots prevent callers from changing a retained payload or growing it after accounting. This budget excludes the bounded Python container overhead; it is not an RSS promise. The 90-second default TTL and authorization/cache-key boundaries stay unchanged.

Four regressions failed on the original implementation: entry capacity, unrelated expired keys, byte capacity, and caller mutation. Focused tests also exercise replacement/clear accounting, concurrent thread-pool access, and the operator report route. No new timer, worker or disk cache is introduced.

The existing ResponseCache API is loader-oriented and does not support atomic replacement with per-entry expiry. This cache retains its small explicit get/set interface instead of simulating a replacement with invalidate followed by get_or_load.

Validation: 39 focused tests passed across tracker_metrics, operator_report, platform_settings and tracker_policy_admin_settings. Ruff and repository module/debt checks passed. A separate read-only caller review found no blocking issue. No production host deployment or RSS benchmark was performed.
