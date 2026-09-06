# Robopark: Armbian installer, autostart, diagnostics, and OTA

**Date:** 2026-09-07

**Status:** approved in chat on 2026-09-07; written specification awaiting final user review

**Scope:** bootstrap archive, host services, Tuna publication, diagnostics and safe repair, Royal OTA, optional GitHub release discovery

## 1. Problem

Robopark currently has a Docker Compose deployment, Tuna wrapper, and a Royal-only ZIP update flow. A new host still requires manual package installation, environment editing, service setup, and troubleshooting. The current containerized `ops-agent` copies a release over the live checkout, does not update itself during cutover, and cannot guarantee an atomic rollback of the complete host runtime.

The target host is initially Armbian 26 on ARM64 with 8 GiB RAM. The same installer must also work after migration to Ubuntu on ARM64 or AMD64 with 32 GiB RAM. The application remains local to the host and is published only through a Tuna HTTPS tunnel.

## 2. Goals

1. Produce one distributable installer archive that prepares a supported host through an interactive wizard.
2. Start Robopark, Tuna, diagnostics, and OTA services automatically after boot and recover from temporary failures.
3. Preserve all host-owned secrets and persistent data across repair and update operations.
4. Let Royal install a signed release ZIP and, optionally, discover GitHub Releases and approve installation from the UI.
5. Allow an OTA release to add or change Python and npm dependencies, Docker build inputs, Compose configuration, migrations, host scripts, systemd units, and the updater itself.
6. Test a candidate before cutover, take a snapshot, switch atomically, and roll back automatically when the new release is unhealthy.
7. Provide actionable diagnostics and safe automatic repairs without deleting or recreating application data or secrets.

## 3. Non-goals

- unattended installation of application releases without Royal approval;
- exposing API, database, Docker, or port 8080 directly to the internet;
- high availability across several hosts;
- replacing Tuna with another tunnel provider;
- solving the planned SQLite-to-PostgreSQL migration inside this installer project;
- destructive database repair or automatic secret rotation;
- treating `git pull` on the production checkout as a deployment mechanism.

The release protocol remains storage-engine aware so a later signed OTA can introduce PostgreSQL and Redis without replacing the installer.

## 4. Selected approach

Robopark uses a hybrid distribution model:

- a self-contained bootstrap archive performs first installation and recovery;
- signed release archives remain the canonical OTA artifact;
- the Royal UI accepts a local release file;
- an optional host-side GitHub checker discovers releases from the configured repository, but installation still requires Royal confirmation;
- a root-owned systemd updater performs privileged cutover instead of a container that mounts the Docker socket.

This is chosen over archive-only delivery because it gives controlled update discovery, and over direct Git deployment because immutable version directories and signed manifests make rollback and audit reliable.

## 5. Host layout

Host-owned state is separated from immutable application releases:

```text
/opt/robopark/
  current -> releases/<version-id>
  releases/<version-id>/
  previous -> releases/<version-id>
  installer/

/etc/robopark/
  host.env
  tuna.env
  updater.env
  release-public-key.pem

/var/lib/robopark/
  data/
  ops/
    inbox/
    staging/
    artifacts/
    rollbacks/
    state/
  diagnostics/

/var/log/robopark/
```

Files below `/etc/robopark` are root-owned, mode `0600` when they contain secrets, and are never included in a release archive. Persistent application data lives below `/var/lib/robopark` or in Docker volumes explicitly bound there. Release cleanup never follows symlinks and never removes `current`, `previous`, host configuration, or persistent data.

## 6. Installer archive

The archive contains:

- `install.sh`, which is the only required entry point;
- focused shell modules for preflight, package installation, configuration, services, Tuna, deployment, diagnostics, and rollback;
- the initial immutable Robopark release;
- systemd unit templates;
- the release verification public key;
- an offline Russian quick-start and checksum file.

The archive does not contain production passwords, Tuna tokens, GitHub tokens, or a release signing private key.

### 6.1 Supported hosts

The installer accepts:

- Armbian based on Debian or Ubuntu, ARM64;
- Ubuntu Server LTS or compatible Ubuntu release, ARM64 or AMD64;
- a system using `systemd`, `apt`, and at least 6 GiB usable storage.

The 8 GiB Armbian profile uses two API workers. Hosts with at least 24 GiB may use four after the wizard asks for the larger profile. Worker count is bounded by the application configuration. The installer reports that capacity for 200 simultaneous users requires a separate load test on the actual host and does not claim capacity from RAM alone.

Unsupported CPU architecture, missing systemd, read-only root filesystem, or less than the minimum free disk stops installation before mutation.

### 6.2 Interactive wizard

The wizard requests values from the terminal without putting secrets in command-line arguments:

- Tuna token;
- Tuna publication mode: reserved subdomain or custom domain; a temporary dynamic URL is allowed only for a test installation;
- Tuna location, default `ru`;
- initial Royal username and password;
- optional shared operator registration password;
- public site origin when it cannot be derived from the selected domain;
- GitHub update discovery: disabled, public repository, or private repository;
- GitHub repository slug and optional fine-grained read-only token;
- standard or large host profile.

The installer generates `SECRET_KEY` locally using a cryptographically secure generator. Secret inputs are not echoed, logged, written to shell history, or included in diagnostic bundles. Existing non-empty secret values are preserved on reinstall unless the operator explicitly chooses a documented rotation flow.

### 6.3 Installation flow

1. Require root through `sudo` and acquire an exclusive installer lock.
2. Detect OS, architecture, memory, storage, DNS, clock synchronization, and outbound HTTPS.
3. Repair an interrupted package-manager transaction with `dpkg --configure -a` only when the preflight identifies that state.
4. Install required packages and Docker Engine with Compose support.
5. Install Tuna from its signed APT repository and verify `tuna help` succeeds.
6. Write configuration atomically through temporary files with restrictive permissions.
7. Install the root-owned updater, diagnostic command, and systemd units.
8. Verify the initial release signature and unpack it into a new release directory.
9. Build and start the local stack, run migrations through the release entrypoint, and wait for readiness.
10. Start Tuna and verify the local endpoint and configured public HTTPS endpoint.
11. Enable all required services and timers for future boots.
12. Print the application URL, service status, installed version, and recovery commands.

Each completed phase is recorded in a non-secret state file. Re-running `install.sh` resumes or repairs the installation rather than overwriting data.

## 7. Autostart and recovery

Systemd owns host lifecycle:

- `robopark.service` starts the Docker Compose stack from `/opt/robopark/current/deploy` and waits for API and web readiness;
- `robopark-tuna.service` starts only after `robopark.service` is ready and forwards to `127.0.0.1:8080` with HTTPS redirect;
- `robopark-updater.service` processes one approved OTA request at a time;
- `robopark-update-check.timer` checks GitHub release metadata every six hours when enabled;
- `robopark-doctor.timer` runs a lightweight diagnostic check daily;
- `robopark-watchdog.timer` checks local readiness every two minutes and restarts only the failing service after consecutive failures.

Services use bounded restart delays and systemd start limits. A permanently failing component enters a visible failed state instead of creating an infinite restart loop. Docker containers keep `restart: unless-stopped`, while systemd remains the owner of stack-level start order.

The Tuna unit never starts before local web readiness. Loss of internet restarts Tuna without restarting the application. Loss of the Tracker or Emergency upstream marks integration health degraded but does not restart Robopark.

## 8. Diagnostics and safe repair

### 8.1 Interfaces

Diagnostics are available through:

- `sudo robopark doctor` for a read-only full check;
- `sudo robopark repair` for allowlisted safe repairs followed by another full check;
- `sudo robopark status` for a concise version, URL, service, resource, and last-backup summary;
- a Royal-only system health panel backed by sanitized API data;
- a downloadable diagnostic ZIP with secrets and user payloads excluded.

Every check returns `ok`, `warning`, or `failed`, a stable machine code, a Russian explanation, and the next action. The full result is stored as bounded JSON and a human-readable log.

### 8.2 Checks

The doctor checks:

- supported OS and architecture;
- clock synchronization, DNS, and outbound HTTPS;
- disk space, inode space, memory, load, swap, and available temperature sensors;
- Docker daemon, Compose, containers, health checks, and restart counts;
- current and previous release symlinks and release manifest consistency;
- configuration ownership, mode, required keys, and cross-file consistency without exposing values;
- database availability, migration revision, integrity/readiness result, and recent backup metadata;
- local web/API endpoints and expected security headers;
- Tuna binary, service, configured route, HTTPS reachability, and certificate expiry;
- updater state, stuck jobs, update source, and last successful/failed update;
- integration readiness for Tracker and Emergency using the application's sanitized health contract;
- log growth and retained diagnostic/update artifacts.

### 8.3 Automatic repair allowlist

`robopark repair` may:

- complete an interrupted `dpkg` configuration and retry a failed package download with bounded backoff;
- restore known systemd units from the trusted installed release, run `daemon-reload`, and re-enable them;
- correct ownership and modes on known Robopark directories and configuration files;
- restart an inactive Docker, Robopark, Tuna, or updater service;
- rebuild the current release when images are missing;
- remove stopped Robopark build containers, dangling images, expired staging directories, old diagnostics, and releases outside retention;
- rotate application and container logs according to configured bounds;
- reconcile a completed update whose final status file was not consumed after reboot.

Repair never:

- deletes, recreates, truncates, or modifies the application database;
- changes or prints passwords, encryption keys, Tuna tokens, integration credentials, or GitHub tokens;
- removes the active or previous release;
- runs `docker system prune --volumes`;
- performs a release upgrade;
- changes firewall, SSH, user accounts, or unrelated host services.

If a failed check has no allowlisted repair, the command leaves state unchanged and prints the exact manual action.

## 9. Release archive and trust

The existing `release` ZIP format advances to a versioned manifest containing:

- application version;
- Git commit SHA;
- release format version;
- minimum compatible installer/updater version;
- database migration head and compatibility metadata;
- file paths, sizes, and SHA-256 hashes;
- required host capabilities;
- creation timestamp and update notes;
- an Ed25519 signature in the separate ZIP member `manifest.sig`, calculated over
  canonical `manifest.json` bytes.

The signing private key never enters the repository or installer. The public verification key is installed in `/etc/robopark/release-public-key.pem`. Both the API and host updater verify the signature, but the host updater is the final trust boundary. Unsigned archives, unknown keys, duplicate ZIP paths, symlinks, special files, path traversal, checksum mismatches, oversized archives, incompatible formats, and unapproved downgrades are rejected before tests or host changes.

Key rotation requires a release signed by the currently trusted key and carrying the next public key plus an activation version. A compromised Royal browser session therefore cannot install arbitrary code without the publisher signing key.

## 10. OTA flow

### 10.1 Manual archive through Royal

1. Royal selects a release ZIP and sees parsed version and release notes.
2. The API stores the upload in the operations inbox with a strict size limit and validates the archive without applying it.
3. Royal types the update confirmation phrase.
4. The API creates an approved update request containing the archive path and current authenticated actor audit data.
5. The root-owned updater re-verifies the artifact independently and executes the cutover.
6. The UI polls sanitized job state and shows validation, build, backup, cutover, health, success, or rollback.

Admin and other roles cannot discover, approve, upload, abort, or inspect update artifacts.

### 10.2 GitHub release discovery

The host checker calls the GitHub Releases API for the configured exact repository. For a private repository it reads a fine-grained, contents-read-only token from `/etc/robopark/updater.env`; the token is never passed to the API container or browser.

The checker downloads only metadata during periodic checks. It records an available version when:

- the release is non-draft;
- prerelease policy matches the configured stable or prerelease channel;
- the required release ZIP, detached signature, and metadata assets are all present;
- the version is newer than the installed version.

The Royal UI displays the version, notes, publication time, and source. Clicking download and install creates an approval request. The host updater then downloads assets, verifies them, and follows the same pipeline as a manually uploaded archive. There is no automatic application of GitHub releases.

Failure to reach GitHub leaves the installed system unchanged and marks update discovery stale. GitHub is not required for startup or manual OTA.

## 11. Candidate validation and cutover

The updater executes these phases under an exclusive host lock:

1. Verify release signature, manifest, compatibility, paths, hashes, and available disk space.
2. Unpack into a unique staging directory on the same filesystem as releases.
3. Validate Compose configuration and build candidate images. New Python/npm dependencies are installed by the candidate Dockerfiles and lockfiles.
4. Run the canonical API and web quality gates supplied by the candidate in an isolated test environment.
5. Start the candidate on private alternate ports with a copy or isolated test database where the migration contract allows it.
6. Run API, web, authentication-shell, and migration smoke checks.
7. Enter maintenance mode and create a consistent snapshot of persistent data and current configuration metadata.
8. Install candidate host scripts and systemd units into a staging host-tools directory and run their self-tests.
9. Atomically move the candidate into `/opt/robopark/releases/<version-id>` and switch `current`, retaining the old target as `previous`.
10. Apply production migrations through a single migration process, then start the new stack.
11. Reload changed systemd units, activate the staged host tools, and restart affected units in a controlled order.
12. Verify local readiness, authenticated shell smoke behavior, and Tuna public HTTPS reachability.
13. Mark success, leave the previous release for rollback, exit maintenance, and apply retention.

An update may replace the updater itself. The running updater first validates and atomically installs the successor, then hands final service restart and job reconciliation to a minimal stable launcher installed by the bootstrap archive. The successor must pass `--self-test` before activation.

## 12. Rollback and interruption recovery

Before writes resume, any failed build, test, migration preflight, start, or health check returns `current` to the previous release, restores the pre-cutover snapshot when the database was changed, reloads the previous host units, and verifies readiness again.

The updater writes a durable phase journal before every mutation. After power loss or reboot it chooses one deterministic action:

- discard incomplete staging when cutover never began;
- finish health verification when the new release is running;
- roll back when cutover began but readiness cannot be proven;
- preserve maintenance mode and report `manual_recovery_required` if both new and previous releases fail verification.

The last two successful releases and their compatible rollback material are retained. Failed staging and diagnostic artifacts have bounded age and size retention.

Database migrations must declare compatibility. A release with a destructive or irreversible migration is rejected by normal OTA unless its manifest identifies a separately implemented maintenance migration procedure. The installer project itself does not introduce such migrations.

## 13. Royal system-health UI

The existing Royal operations panel gains:

- installed version and Git SHA;
- status for application, database, Tuna, updater, last backup, disk, and memory;
- last diagnostic time and failed check count;
- available GitHub version and release notes;
- manual ZIP validation preview;
- explicit update approval and live progress;
- last rollback reason;
- diagnostic ZIP download;
- buttons to request a new read-only diagnostic run and allowlisted repair job.

The browser never receives host paths, raw environment values, process command lines, access tokens, cookies, or secret-containing logs. Repair and update events are written to the existing audit log with the Royal actor where applicable.

## 14. Error handling

- All installer and updater phases use stable error codes and Russian operator messages.
- Network operations use timeouts, bounded retries with backoff, and partial-download cleanup.
- Concurrent install, repair, snapshot, restore, and update jobs are rejected through exclusive locks.
- A low-disk preflight estimates space for the candidate, image build, snapshot, and rollback before download or build.
- Tuna failure never triggers database rollback after a locally healthy application cutover; it marks publication degraded and restores the previous Tuna unit if its configuration changed.
- An upstream Tracker or Emergency outage does not fail installation or OTA when core readiness and configuration are valid; it appears as a degraded integration.
- Diagnostic and OTA logs redact known secret names and never include environment dumps.

## 15. Packaging and release automation

Repository scripts produce:

- `robopark-installer-<version>.tar.gz`;
- `robopark-release-<version>.zip`;
- detached signatures;
- SHA-256 checksum files;
- release metadata suitable for GitHub Release assets.

Local packaging accepts the signing private key only through a protected file path or CI secret. GitHub Actions may build and sign release assets when the signing secret is configured. CI never prints the key and does not publish an unsigned production release. GitHub assets additionally include an Ed25519 signature for the complete release ZIP, allowing the host checker to authenticate the download before opening it; a manually uploaded ZIP remains self-contained through its internal `manifest.sig`.

The installer archive embeds the exact initial signed release and matching public key. A verification command checks an archive without installing it.

## 16. Verification strategy

Implementation follows test-first cycles for behavior-bearing scripts and code.

Automated checks cover:

- shell syntax and a fake-command harness for installer phases on a temporary root;
- supported/unsupported OS and architecture detection;
- idempotent reinstall and interrupted-install resume;
- secret preservation and diagnostic redaction;
- systemd unit dependencies, restart limits, enablement, and timer schedules;
- doctor status classification and every repair allowlist entry;
- release signing, tampering, wrong key, path traversal, symlinks, duplicate paths, size limits, downgrade, and key rotation;
- manual Royal upload permissions and approval;
- public/private GitHub metadata behavior without exposing tokens;
- dependency, Dockerfile, Compose, migration, host-script, unit, and updater replacement through OTA;
- build/test failure before cutover;
- health failure after cutover and complete rollback;
- reboot recovery at every durable update phase;
- Docker Compose validation and production image builds;
- API tests, web tests, build, lint, and focused browser smoke flows.

Linux integration runs in Debian/Ubuntu environments for ARM64-compatible scripts. Final acceptance on the target Armbian device runs installation, reboot, Tuna publication, Royal update, deliberately failed update, rollback, repair, and diagnostic-bundle exercises. Capacity for 200 simultaneous users is measured separately against the target hardware and current storage backend.

## 17. Acceptance criteria

1. A clean supported Armbian host reaches a healthy local Robopark and Tuna HTTPS URL from one `sudo ./install.sh` session.
2. Reboot restores application, Tuna, updater, and timers without manual commands.
3. Temporary network or Tuna loss recovers without restarting or corrupting the application.
4. `robopark doctor`, `status`, and `repair` return useful Russian output and never expose secrets.
5. Re-running the installer preserves database, attachments, configuration, and secrets.
6. Royal can install a valid signed local ZIP; every other role is denied.
7. GitHub discovery reports a newer release and never installs it without Royal approval.
8. OTA successfully installs a release that changes dependencies, Dockerfiles, migrations, host scripts, systemd units, and the updater.
9. Invalid, tampered, incompatible, unsigned, or failing candidates leave the current system unchanged.
10. A post-cutover health failure automatically restores the previous compatible code, host tools, service definitions, and pre-cutover data state.
11. Power interruption during any update phase resolves to a healthy current or previous release, or a clear maintenance state with recovery instructions.
12. The produced installer and release artifacts have checksums, signatures, version metadata, and offline instructions.

## 18. Delivery boundaries

This specification is implemented as one coordinated program, split during planning into independently testable increments:

1. host layout, installer wizard, initial deployment, and autostart;
2. doctor, status, repair, watchdog, and sanitized health API/UI;
3. signed immutable release format and host updater with rollback;
4. Royal OTA integration and GitHub release discovery;
5. packaging, full failure testing, documentation, and final installer archive.

Each increment must keep the existing manual deployment usable until the new path reaches its acceptance gate.
