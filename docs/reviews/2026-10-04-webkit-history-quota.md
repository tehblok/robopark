# WebKit work-tab cycle verification

Date: 2026-10-04.

The rc.29 full CI ran 671 cross-browser workflows successfully, but the WebKit
50-cycle draft/File retention scenario reloaded the document. Eight local
uninstrumented repetitions also failed. Lifetime instrumentation showed a new
Window, not just a React subtree replacement.

A diagnostic run with 25 prior same-URL history entries captured the exact chain:
History mutation 101 raised SecurityError (more than 100 pushState calls per
10 seconds), React Router performed a main-frame document navigation, and the
previous Window and in-memory File were lost. Additional instrumentation slowed
three other runs beyond that window and they passed; those passes alone were not
accepted as a fix.

WebKit applies one shared 100-per-10-second limit to pushState and replaceState:
[History.cpp](https://github.com/WebKit/WebKit/blob/main/Source/WebCore/page/History.cpp).
The installed React Router 7.18.4 browser-history push implementation falls back
to location.assign for errors other than DataCloneError. Replacing push with
replace would not bypass WebKit's quota and would change Back semantics.

The final test retains all 50 Open/Task cycles, draft text, selected File,
interval, read and zero-write assertions. Each tab must become selected before
the next click. Only WebKit waits at cycle 25 until 10.25 real seconds have elapsed
since setup completed, splitting the 100 generated history writes across quota
windows. A new assertion requires exactly one main-document request. No production
routing changes, increased assertion timeout, test retry or skip were introduced.

Verification: 15 focused runs passed (5 each in Chromium, Firefox and WebKit),
each with 50 cycles and no reload. Independent review found no blockers.
