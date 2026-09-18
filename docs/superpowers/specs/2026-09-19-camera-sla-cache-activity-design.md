# Camera, SLA, navigation, and activity design

## Scope and priorities

Restore camera scanning, make the repair SLA accurate, preserve useful screens across navigation, remove redundant success noise, and expose limited last-activity metadata to user managers. Keep the existing React/FastAPI architecture and the signed OTA path. Do not turn the service worker into an authenticated API cache.

Exact GPS is outside this release. The browser cannot provide it without device/browser permission; an approximate site label inferred from a known network is a separate decision. This release records the observed public IP and user-agent/device, never claims they prove presence at a park or office, and makes no external GeoIP requests.

## Camera

The shipped nginx `Permissions-Policy` currently says `camera=()` on the app shell, so `getUserMedia` is denied independently of a browser's stored preference. Permit camera for this origin on the document, retain microphone disabled, and leave geolocation disabled until a location design is approved. The scanner remains a user-initiated action and must dispose all tracks on close, navigation, error, and backgrounding. It must fall back from unavailable rear camera and unavailable native barcode detection, retain manual number entry, and report policy/permission/device/decode errors distinctly. Photo upload stays available as a file picker as well as a phone camera input.

## Repair SLA

The source is the latest actual Tracker transition into «В очереди». The clock consumes five working hours in `Europe/Moscow`, daily 09:00–21:00. A transition outside the window starts counting at the next 09:00. A deadline is not fabricated from issue creation when the transition is unknown; the UI shows «Нет данных о начале очереди». The existing server response carries the deadline; list and detail render the same rule. A shared page-level minute tick updates the displayed remainder without a network request or per-card timer. Ongoing time outside work hours does not reduce the remaining SLA.

## Navigation and mutations

Use the existing bounded in-memory resource store as the one cache for authenticated data. Keys must include principal and access scope. Revisiting a page paints its previous response immediately; stale data revalidates in the background, with a visible refresh/error indication that does not blank the page. Do not evict merely because a route unmounts. Evict promptly on logout, principal/access change, 401/403, and affected mutations. Mutations keep the current view mounted and refresh only affected resources; no full document navigation. Coalesce duplicate loads and avoid simultaneously refreshing hidden pages. Preserve existing no-disk storage policy for protected responses. Check request counts on repeated navigation and on a 200-session load test.

## Interface

Suppress success-state `Сохранено` badges and other repeated confirmation text, but preserve pending/failure indicators. Remove the redundant visible «Робопарк» brand from shell/login/footer and watermark copy without removing the user-identifying confidentiality watermark. Correct phone spacing and touch target sizing in the affected screens. Add short, unobtrusive transitions for overlays and route content; honor `prefers-reduced-motion` and do not delay data rendering.

## User activity

Store one latest-activity record per user: server-observed IP, bounded/sanitized user-agent or derived device label, and timestamp. Update on successful login and at a throttled interval during authenticated use, not for every request. Show it only through the existing user-management permission gate; never expose it in public/auth endpoints. Show an explicit «нет данных» state. Do not retain a trail of IPs or user agents and do not call a third-party lookup service. Approximate park/office network labels and GPS are deferred until their inputs and trust limits are agreed.

## Verification

Add regression tests that fail against the current camera policy, current calendar-hour deadline, static timer, and route-unmount cache eviction. Test user activity visibility, throttling, absent metadata, and role denial. Verify nginx configuration, API/Web tests, production build, mobile and desktop UI behavior, and representative repeated-navigation/request-count checks before issuing a new signed release artifact.
