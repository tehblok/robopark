# Workspace OTA notes within the v1 manifest bounds

Date: 2026-10-04.

The full rc.29 CI exposed 16 workspace install/update archive failures. The
release metadata contained 380 characters of notes; the install snapshot builder
appended a source digest and provenance to the same manifest item, producing
more than the existing 500-character limit. A focused build reproduced
ota_manifest_invalid before the repair.

Workspace builders now pass explicit changes to the shared OTA builder. Complete
version-adjusted release notes are split at word boundaries (or at 500 characters
for a long unbroken string); source SHA-256 and SOURCE-SNAPSHOT.json provenance
are separate items. The existing maximum of 100 items is checked including
provenance. Too much content fails rather than silently dropping it.

The local-update builder also stops replacing current release notes with an
obsolete fixed Tuna/0056 description. Normal release builds keep the original
single-item notes behavior. Manifest schema, unsigned v1 format, digest checks
and all standalone/API/host validation limits remain unchanged.

Validation: 44 workspace install/update tests, 43 OTA builder/parity tests and
two current-archive checks passed. Boundary coverage includes a valid 500-character
note and growth beyond 500 after a workspace version replacement. Independent
read-only review found no blockers.
