# Service worker: graceful CacheStorage failures

## Confirmed behavior

The worker previously rejected a hashed asset request when CacheStorage read,
open, write, enumeration or eviction failed, even when the network response was
successful. Navigation similarly failed if the shell cache could not be opened
or read. Nine regression cases reproduced these failures before the patch,
including a terminal bundle entering the runtime cache despite its exclusion
from precaching.

## Change

Cache access is best effort during fetch. A successful network response remains
usable after a storage failure, while an actual network failure remains visible.
Navigation still prefers the installed cached shell so waiting updates cannot mix
HTML and chunks from different releases. When neither it nor the network is
available, the existing offline page is used if readable. Terminal HTML and its
isolated bundles bypass both cache paths.

Installation/activation handshakes, the 100-entry runtime cap, same-origin
hashed-asset admission, and API/private attachment exclusions remain intact.

## Verification

- The nine regression cases failed on the previous worker.
- Unit coverage includes every cache failure point, a true network failure,
  navigation without readable cache, readable offline fallback and terminal
  bundle bypass.
- All 53 frontend script tests, lint, production build and diff checks pass.
- All 48 production PWA checks passed in Chromium, Firefox and WebKit
  using the pinned Linux image (3.6 minutes); the test container was removed.
- Independent static review found no blockers; its suggested HTTP 401/503
  preservation cases were added and pass.
- No live host installation or hardware performance claim is involved.
