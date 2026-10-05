# Report photo restore and browser synchronization

A full three-engine workflow run passed 669/672 scenarios. Focused repetition
then separated three causes:

- The mobile lifecycle opened detail while the queued claim POST was still
  being accepted by the single-threaded diagnostic bridge. The existing
  server-action assertion now precedes navigation. The later Tracker outage,
  retry and drain checks remain.
- Firefox also returned 43.9998779296875 for a 44 CSS px touch target.
  Geometry comparison permits only 0.001 CSS px rounding error.


Photo retry was not just a Playwright URL-rewrite problem. The original HTTP
fixture reproduced 5 failures in 10 WebKit attempts; a same-origin HTTP fixture
without page.route reproduced 4/10. In each failure the second multipart body
did not finish within the assertion deadline. Chromium and Firefox passed the
same-origin scenario 10 times each. This proves the native browser path can
stall; it does not establish a measured 90-second production timeout.

ReportForms now reads persisted bytes and constructs an independently backed
File before it can be stored and uploaded again. Twenty repeated same-origin
WebKit workflows then passed, including two reloads, first upload failure,
retry to the same report ID, and exact multipart bytes.

Restoration rechecks account/park generation, unmount and edit revision after
reading bytes. Failed file reads restore text and the acknowledged report ID,
leave the original IDB draft intact, and allow a replacement file. The
attachment-type selector remains locked during restore. Two reviewer findings
were reproduced red and fixed; the full ReportForms unit set passes 21 tests,
including delayed owner/discard and failed-read recovery. Lint and production
build pass. Independent follow-up review found no remaining concrete blocker.

The byte copy is local to the active form, with the existing 15 MiB photo /
64 KiB log limits on persisted drafts; no new persistent cache was added.
Blob reading itself is not abortable, so stale completions are discarded.
No physical Safari/device acceptance or eight-hour soak is claimed.

## Final browser regressions

The mobile task lifecycle passed in Chromium, Firefox and WebKit after waiting
for claim acceptance and correcting subpixel comparison. The final photo
implementation (including failed-read recovery) also passed on all three engines.

The image test still detached its frame after the first attempted ordering fix.
Independent inspection found the actual cause: the legacy /robots/:vin/check
URL redirects in a passive effect to /robots/:vin; AppRouter keys route
boundaries by route ID, so the entire robot workspace remounts. The image
selection test now uses the canonical URL. Legacy redirect behavior remains
covered by RobotWorkspace unit tests. All six views passed five WebKit repeats
each (30 checks), with dimensions, single requested WebP and marker assertions
unchanged. No production routing behavior was altered.
