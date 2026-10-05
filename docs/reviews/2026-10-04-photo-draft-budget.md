# Photo-draft owner-change storage budget

Date: 2026-10-04

A report draft stored under an existing physical account/park key may belong to a
previous principal. Replacing it preserves the previous principal's draft under
a retired key. The storage admission check previously counted only the new
active draft and other keys, omitting the draft preserved from the occupied slot.
This could exceed the existing limit of eight drafts or 60 MiB of photo bytes.

The admission calculation now models the final retained records inside the same
IndexedDB readwrite transaction. It includes the occupied draft when it will be
archived, and excludes a prior retired record if that slot will be overwritten.
The limits, owner binding, generation fencing and incomplete-draft preservation
policy are unchanged. A rejected replacement aborts before any persisted writes.

Verification:
- Two new regressions failed before the fix: record count and photo bytes at
  owner change. Both now reject while preserving the previous active draft.
- Additional cases cover a permitted owner change, replacement of an existing
  retired slot, and a same-owner update at the full eight-record limit.
- Reports and report-form unit suites: 51 passed.
- Targeted lint and TypeScript build passed.
- Independent read-only review found no remaining blocker.

No live browser data was changed.
