# Atomic review completion comment

Date: 2026-10-04. Prepared after rc.29; not included in that release candidate.

The non-structured review form previously enqueued its completion comment first
and then enqueued the review plus photo with the comment action as a dependency.
The comment could confirm before the second enqueue. Concurrent storage-pressure
cleanup could legitimately evict that unreferenced receipt, leaving the review
with a dependency that no longer existed locally.

Both review form variants now embed the trimmed completion comment in the
submit_review payload. The existing enqueueMedia path atomically persists the
review and photo, with only the photo dependency. The server already stages the
completion comment and review together. Generic task-chat comments and previously
persisted actions keep their existing behavior; no schema migration is required.
The unused commentActionId builder option was removed to prevent reintroducing
this split producer path.

Verification:

- The UI regression failed before the fix: it observed a separate comment action
  and a review lacking the embedded comment. Both existing-comment and required-
  new-comment variants now pass without a standalone enqueueAction call.
- IssueWorkbench, offline action builders and SubmitReviewForm: 159 tests passed.
- The test for legacy phone write-off now waits for the asynchronously populated
  part selector before selecting it; it previously raced that loading state.

The API media/offline subset (64 passed) includes the embedded completion
comment and review effects plus receipt replay without duplicate effects.
Frontend lint and production build passed. Independent review found no blockers.
