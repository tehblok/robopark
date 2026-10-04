# Installed-release instructions without a stale archive hash

Date: 2026-10-04.

The repository README is updated after building an OTA so its one-command
installer can pin the final archive digest. Copying that README into the next
archive retained the previous candidate's digest. It also linked relative docs/
paths, although those documentation files are not shipped in the runtime payload.

The next slice packages deploy/ota/README.installed.md as release/README.md.
This short guide explains how to run an already verified archive and links to
online documentation. It contains no self-referential digest or download URL
for a potentially unpublished candidate. The repository README still carries the
exact published archive digest for the one-command download and verification.

The mapping is created independently of the root README's tracked-file status.
Deleting or replacing that root file with an external symlink in a workspace
snapshot still produces the stable guide. The template itself must be a regular,
non-symlink file. It remains a tracked source member at its deploy path as well;
both manifest entries use ordinary size and hash checks. No OTA v1 schema or
installer behavior changes.

Validation: the original archive-content regression failed on the old README.
The initial builder/install/update set passed 72 tests. After review required an
unconditional mapping, all 30 builder tests passed, including actual tracked-root
deletion and external-symlink replacements. Independent re-review found no
remaining blockers.

Final full host suite: 1,281 tests passed.
