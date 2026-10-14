# AGX production resilience

Scope: preserve the 2026-10-14 host repair in the latest production source and deploy a patch release.

1. Regression tests for the two boot-order cycles, bounded network retries, profile guards and cooldown.
2. Persist recovery helper outside release directories; keep profile selection host-local. Reconcile sleep, Wi-Fi and journald policy through a privileged systemd setup unit.
3. Integrate installation, OTA activation and local OTA transition from the older installed updater. Keep persistent policy through app rollback.
4. Run host/OTA regression suites, Linux systemd graph verification, package verification and review.
5. Commit to the production line, publish patch release, apply its OTA on AGX and verify readiness, bot, timer and boot graph. Record evidence in the existing project wiki.

No deliberate outage or reboot is part of acceptance. No database migration is added.
