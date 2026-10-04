# PWA activation readiness

The Firefox production-PWA test could observe an active controller in the old
document immediately before the application's safe activation handler reloaded
that document. Its one-shot runtime-cache deletion then lost the execution
context before offline mode began.

The test now polls that idempotent deletion, retrying only Playwright's
`Execution context was destroyed` navigation error. It then rechecks the
active worker and task heading before going offline. Other errors still fail
the test. The PWA config retains a trace for every failure because the Linux
wrapper does not forward `CI`, so retry-only tracing produces no evidence.
