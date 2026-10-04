# Report-photo scope movement across browser tabs

Date: 2026-10-04

The scope quarantine and restoration helpers enumerated drafts in a readonly
transaction, then moved those snapshots in separate readwrite transactions.
A second tab could update a draft between those operations. Quarantine could
archive the stale version or delete a replacement principal's active draft;
restoration could overwrite a newer archived version with stale content.

The scan now only discovers candidate keys. Each writing transaction rereads
the active or archived record, checks its current scope and encoded owner, and
moves that fresh value atomically. Restoration still leaves any existing active
draft untouched.

For quarantine, the in-memory scope epoch increment and lease deletion remain
unconditional for each key matched by the initial scan, before the writing
transaction. This fences an already queued stale writer from that scope even
if another tab has replaced its draft. The persisted generation only rotates
when the fresh record still belongs to the quarantined scope; another owner's
active draft and generation are preserved.

Verification:
- Two deterministic regressions failed before the correction with stale title
  and revision: updated same-owner quarantine and updated archive restoration.
- A third case switches owners after the scan and queues a stale former-owner
  write. The replacement owner's draft remains intact.
- Final draft suite: 18 passed, including the owner-change storage-budget cases.
- Drafts, protected-storage registry and ReportForms combined: 51 passed.
- Oxlint and production build passed.
- Independent review of the final diff confirmed the in-memory fence and
  persisted-generation distinction; no remaining blocker.

These checks used isolated local IndexedDB fixtures; no live user drafts changed.
