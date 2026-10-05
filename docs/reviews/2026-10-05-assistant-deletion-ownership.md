# Assistant deletion and answer ownership

Date: 2026-10-05. Scope is the current conversation lifecycle fix, not a new audit.

Five deterministic regressions failed before the fix: a final answer detail
replaced a newer selection; late deletion cleared a newer selection; two
parallel deletions restored an item from a stale list; initial detail reopened
a deleted conversation; duplicate delete clicks sent two requests. A sixth
regression showed that returning to an unfinished conversation never resumed
its job polling.

Deletion now updates the current list and clears only its currently selected
conversation. Successful deletion synchronously aborts open, submit and resumed
polling; failure retains the conversation and allows retry. The final submit
read checks cancellation after awaiting its response. Resumed job bookkeeping
holds one id rather than an accumulating Set and resets on conversation changes.
Already-sent mutations are not described as server-cancellable.

Validation of the final change:

- 61 assistant UI/API-client unit tests, including deletion during resumed
  polling and overlapping creation/deletion.
- 27 API job/control tests, including deletion during actual worker publication
  flow with the inference call stubbed: no conversation, message or job returns.
- 18 native Linux browser cases in Chromium, Firefox and WebKit, including
  creation/deletion failures, retry and late responses after changing selection.
- Frontend lint/build, API Ruff check and format check passed.
- Independent read-only review found no blocking regression.

Local evidence: output/verification/ota-contracts/ai-conversation-deletion-*,
ai-conversation-resume-red.log, ai-delete-inference-api.log and
ai-conversation-final-browser*. No live host installation or AGX inference.
