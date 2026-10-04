# Visual baseline review

The first sharded CI run produced 67 screenshot differences. Pixel comparison
at a luminance delta over 15 found 49 with no changed pixels to the right of
x=272; representative light/dark screenshots were also inspected. These match
the added assistant navigation item and the shifted sidebar items. The campaign,
reports-new and long admin pages have no separate content change in those
artifacts. No pixel tolerance is relaxed.

Four system screenshots include the previously added host performance section,
terminal navigation and updated introductory text. Their increased full-page
height matches those additions. Work screenshots include the assistant link;
it has now moved below the main actions and history so the primary controls
remain visible together on a 320px phone (browser contract passed). Fresh work
baselines require inspection after that placement change.

Removed 80 unused screenshot files (8,422,022 bytes): legacy `a-` baselines and
the removed `classic-map` route. The current test enumerates 21 routes, two
themes and two snapshot widths, resolving exactly 84 filenames. All 84 remain
present. Searches found no other producer or consumer of the deleted baselines.
Design mockups are separate and are unchanged. These files are excluded from
OTA payloads already; this reduces checkout/CI workspace clutter, not host disk
usage. Git history retains previous images.

The final pinned Linux regeneration run passed 125 selected visual scenarios,
including responsive geometry and accessibility assertions. Fresh 320px light
and 1440px dark work screenshots were inspected: main actions and history remain
above the assistant link, with no overlap. Only the 67 previously classified
meaningful baseline changes were retained; incidental raster changes outside
that reviewed set were restored. No acceptance threshold was changed. Final CI
will compare the committed baselines in ordinary verification mode.
