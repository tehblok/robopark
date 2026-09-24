# Task 10 report — resilient camera, barcode and photo workflows

## Result

- Added gallery QR scanning with lazy native/jsQR detector selection, while keeping manual entry available for missing, denied and insecure camera paths.
- Camera requests prefer the rear camera and preserve cleanup on detection, cancel, visibility change, navigation/unmount, late stream resolution and playback failure.
- Completion, component and part photo inputs now offer rear-camera capture; completion keeps one explicitly replaceable photo and local preview.
- Completion photos are converted off-thread to WebP at a maximum 1920 px edge and 82% quality. QR/document media bypass lossy conversion; conversion failures retain and upload the original.
- Converted completion media retains its original blob in the scoped offline media record until successful upload acknowledgement, then removes only `originalBlob`. Existing scope isolation, pending/attention transitions and activation-fence inputs are unchanged.

## Files

- `apps/web/src/domains/robots/RobotScanner.tsx`
- `apps/web/src/domains/robots/robotScannerFallback.ts`
- `apps/web/src/domains/robots/RobotScanner.test.tsx`
- `apps/web/src/domains/work/SubmitReviewForm.tsx`
- `apps/web/src/domains/work/SubmitReviewForm.test.tsx`
- `apps/web/src/domains/work/IssueWorkbench.tsx`
- `apps/web/src/domains/inventory/InventoryManageView.tsx`
- `apps/web/src/pwa/media.worker.ts`
- `apps/web/src/pwa/mediaPipeline.ts`
- `apps/web/src/pwa/mediaPipeline.test.ts`
- `apps/web/src/pwa/offlineTypes.ts`
- `apps/web/src/pwa/offlineDb.ts`
- `apps/web/src/pwa/syncEngine.ts`
- `apps/web/src/pwa/syncEngine.test.ts`
- `apps/web/e2e/operational/robots.spec.ts`

## RED evidence

Command:

`npm test -- --run src/domains/robots/RobotScanner.test.tsx src/domains/work/SubmitReviewForm.test.tsx src/pwa/mediaPipeline.test.ts`

Observed: 3 files failed, 8 tests failed and 18 passed. Expected failures were missing gallery input, missing `capture="environment"`, 2048 instead of 1920, no QR/document lossless option, and propagated worker conversion failure.

Command:

`npm test -- --run src/pwa/syncEngine.test.ts -t "retains the original media"`

Observed: 1 failed, 23 skipped. The confirmed media record still contained `originalBlob`, proving the acknowledgement cleanup test was active.

## PASS evidence

Command:

`npm test -- --run src/domains/robots/RobotScanner.test.tsx src/domains/robots/robotScannerFallback.test.ts src/domains/work/SubmitReviewForm.test.tsx src/domains/inventory/InventoryManageView.test.tsx src/pwa/mediaPipeline.test.ts src/pwa/syncEngine.test.ts`

Result: 6 files passed, 76 tests passed.

Command: `npx tsc -b --pretty false`

Result: exit 0, no diagnostics.

Command:

`npx playwright test e2e/operational/robots.spec.ts --grep "scanner cancellation stops the fake camera track" --timeout=30000`

Result: 1 Chromium test passed in 2.0 s.

Command: `git diff --check`

Result: exit 0.

Command: targeted `npx oxlint` over changed production files.

Result: exit 0; one pre-existing `react-hooks/exhaustive-deps` warning remains at `IssueWorkbench.tsx:1330` for `accessPrefix`, outside Task 10.

## Self-review

- Verified gallery scanning does not request camera access and closes its `ImageBitmap`.
- Verified late camera streams and active tracks stop during close/navigation; corrected a transient detector-resolution ordering regression caught by the existing test.
- Verified original bytes are counted in the offline budget, persist through `uploading`, survive failure/attention paths, and are cleared only after upload acknowledgement.
- Verified confirmed/pending/attention state rules and scoped database keys were not changed.
- Verified component and part photo state remain separate; no shared replacement state was introduced.

## Concerns

- Targeted oxlint reports the existing unrelated `accessPrefix` hook dependency warning in `IssueWorkbench.tsx:1330`; this task does not change that effect.
- Playwright coverage is intentionally limited to the single requested robots scanner case; no full or long-running E2E suite was run.
