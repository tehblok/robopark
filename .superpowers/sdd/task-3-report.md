# Task 3 Report: CSS — mobile shell, safe-area, forms, KPI, hover

**Branch:** `feature/mobile-responsive`  
**Date:** 2026-08-24  
**Status:** ✅ Complete

## Summary

Implemented mobile-responsive CSS in `apps/web/src/index.css` per task brief. Mobile-only chrome is hidden on desktop and activated at `max-width: 900px`. Safe-area insets, bottom nav, more-sheet, form stacking, KPI grids, and touch-safe hover behavior are in place.

## Changes

### 1. CSS tokens (`:root`)

Added:
- `--bottom-nav-height: 3.75rem`
- `--safe-bottom: env(safe-area-inset-bottom, 0px)`

### 2. Hover guard (touch devices)

Replaced unconditional `transform: translateY(-1px)` on button hover with:

```css
@media (hover: hover) and (pointer: fine) { ... }
```

Excluded `.mobile-nav-item` from hover transform and from global brand `button` fill/hover rules so «Ещё» is not an orange pill.

### 3. Mobile shell (≤900px)

- Replaced old sidebar-as-top-grid block with hide-sidebar + bottom nav layout
- Desktop defaults: `.mobile-bottom-nav`, `.mobile-user-open`, `.mobile-more-backdrop`, `.mobile-more-sheet` → `display: none`
- Mobile: fixed bottom nav (5 columns), safe-area padding, more-sheet/backdrop, `.mobile-user-open` in topbar
- `.app-content` bottom padding accounts for nav + safe area
- Forms/actions stack vertically; panel inputs full-width with `min-height: 2.75rem`
- KPI/stat grids: 2 columns at 900px, 1 column at 480px
- Table wrappers get horizontal scroll with `-webkit-overflow-scrolling: touch`

### 4. Preserved

- Existing dashboard `@media (max-width: 900px)` grid-areas block (chart → kpi → moving) unchanged

## Verification

```bash
cd apps/web && npm run build
```

**Result:** PASS (tsc + vite build, 68 modules, ~90ms)

## Commit

```
feat(web): mobile shell CSS with bottom nav and safe-area
```

File: `apps/web/src/index.css`

## Notes / follow-ups

- `.mobile-more-backdrop` and `.mobile-more-sheet` remain `display: none` on desktop; JS toggling visibility on mobile may need `display: block/grid` when open (Task 2 DOM should handle via conditional render or future JS task).
- Dashboard KPI grid at 900px is now 2 columns (from this task) instead of the prior 3-column rule in the dashboard-only block — intentional per brief mobile KPI spec.

---

## Review fixes (Important findings)

**Date:** 2026-08-24  
**Status:** ✅ Fixed

### Changes

1. **`apps/web/src/index.css`** — In `@media (max-width: 900px)`, added `.mobile-more-backdrop { display: block; }` so the dim overlay paints and receives taps (desktop default remains `display: none`).
2. **`apps/web/src/index.css`** — Excluded `.mobile-user-open` from global brand button fill/hover selectors (`:not(.mobile-user-open)`), same as `.mobile-nav-item`.
3. **`apps/web/index.html`** — Added `viewport-fit=cover` to viewport meta for iOS `env(safe-area-inset-bottom)`.

### Verification

```bash
cd apps/web && npm run build
```

**Result:** PASS

```
npm notice run web@0.0.0 build
npm notice run tsc -b && vite build
vite v8.2.2 building client environment for production...
transforming...
✓ 68 modules transformed.
rendering chunks...
computing gzip size...
dist/index.html                   0.45 kB │ gzip:  0.32 kB
dist/assets/index-D59GmTkx.css   15.05 kB │ gzip:  3.62 kB
dist/assets/index-hz0Sgniq.js   313.31 kB │ gzip: 92.63 kB

✓ built in 92ms
```

### Commit

```
fix(web): show mobile more backdrop and unbrand user chip
```
