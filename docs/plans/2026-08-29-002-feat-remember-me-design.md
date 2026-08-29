# Remember me — design

Date: 2026-08-29

## Goal

Login can keep the user signed in across browser restarts, as long as they use the app at least once every 3 days. Without the checkbox, closing the browser ends the session.

## Decisions

- Idle timeout is 3 days of no authenticated API requests (sliding).
- Unchecked: session cookie (no `Max-Age`). Server still applies the same idle timeout if the browser stays open.
- Checked: persistent cookie with an absolute 30-day cap from login. Idle 3 days is enforced only in the database.
- Cookie is not refreshed on every request. After idle expiry the browser may still send a stale cookie; the API returns 401.
- `expires_at` in `sessions` is the idle deadline and is bumped on authenticated use, at most once per hour.
- Absolute cap: `created_at + session_absolute_ttl_seconds` (30 days). Both conditions must hold.
- Default checkbox: off. Preference is not stored in localStorage.
- Change-password keeps the current session and refreshes `/me` instead of calling login again.
- Existing 14-day sessions remain valid until their current `expires_at`.

## Settings

| Setting | Default | Role |
|---|---|---|
| `SESSION_IDLE_SECONDS` | 259200 (3 days) | Sliding DB expiry |
| `SESSION_ABSOLUTE_TTL_SECONDS` | 2592000 (30 days) | Persistent cookie `max_age` and hard cap |
| `SESSION_TTL_SECONDS` | removed | Replaced by the two above |

`session_slide_min_interval_seconds` = 3600 (not necessarily env-exported).
