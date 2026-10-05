# Assistant conversation creation ownership

Date: 2026-10-05. Scope: ChatPanel creation, not model/hardware acceptance.

Three deterministic regressions failed before the change: repeated New clicks
sent duplicate create requests; automatic issue creation left New/composer
available; a late create response replaced a subsequently selected conversation.

A single in-flight create owner now handles both automatic and manual creation.
The pending state disables New and the composer. Selection, context changes and
unmount invalidate its response. Failure is displayed with an explicit retry,
and the draft survives failure. List updates use the current state rather than
a snapshot captured before the request. Already-sent POSTs can still complete
on the server; logical cancellation does not claim to retract that mutation.

Validation:

- 49 AssistantPage tests, including creation failure/retry, draft preservation,
  issue-context switch, selection and unmount. Three initial regressions were
  observed failing against the previous source.
- 15 native Linux browser cases across Chromium, Firefox and WebKit, including
  failed creation, retry and delayed response after selecting another session.
- Frontend lint and production TypeScript/Vite build passed.
- Independent read-only ownership review found no blocker; its requested
  issue-context regression was added and passed.

Local evidence: output/verification/ota-contracts/ai-conversation-creation-*.log.
No host installation or physical AGX inference was performed.
