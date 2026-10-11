# Linux admin tools

These tools manage **this project's Docker Compose deployment** on Linux.
They do not manage Helm releases, install Docker on the host, change firewall
rules, provision proxy accounts, or install cron entries automatically.
All five entrypoints work from any current directory and use Python's standard
library; Bash 4+, Python 3.10+, Git, Docker Engine, and Compose 2.24.4+ are required.
The container application itself uses Python 3.12. Use a dedicated Linux VM
or host, and keep the Git checkout clean for upgrades.

| Entrypoint | Purpose | Default behavior |
|---|---|---|
| `wizard.sh` | Interactive configuration and eight presets | Saves private configuration; does not start services |
| `installer.sh` | Preflight, build, start, readiness check, baseline snapshot | Confirms installation; preserves existing settings |
| `intaller.sh` | Compatibility alias for the requested spelling | Executes `installer.sh` |
| `updater.sh` | Git update checks, deliberate upgrades, scheduled snapshots | Checks only; requires `--apply` to deploy |
| `remove.sh` | Stop/remove, purge managed volumes, or restore snapshots | Preserves volumes; purge/restore are explicit options |

Use `--help` on any entrypoint. `--dry-run` prints the intended preset/project
and Compose command selection without running Docker, changing files, or
contacting GitHub. It is a plan, not a host readiness check. Mutating operations
take a nonblocking file lock, so overlapping cron/update/removal jobs fail safely.
No `.env` file is sourced as shell code, and secrets are never printed by the wizard.

## Presets

| Preset | Marzban identity source | Apply mode | Poll / probe validity |
|---|---|---|---|
| `existing-sqlite` | Read-only local live SQLite | Manual | 60s / 3600s |
| `existing-mysql` | Restricted live MySQL reader | Manual | 60s / 3600s |
| `existing-sqlite-auto` | Read-only local live SQLite | Automatic | 60s / 3600s |
| `existing-mysql-auto` | Restricted live MySQL reader | Automatic | 60s / 3600s |
| `existing-strict-probe` | Read-only local live SQLite | Automatic | 30s / 300s |
| `low-memory` | Read-only local live SQLite | Manual; controller 256 MB / 0.5 CPU | 120s / 3600s |
| `stack-lab` | Bundled Marzban SQLite | Manual; private APIs | 60s / 3600s |
| `stack-tls` | Bundled Marzban SQLite | Manual; Caddy TLS panel + VPN routes | 60s / 3600s |

Active exits are normally re-probed after half the probe-validity interval.
The stricter preset increases probe activity; it is not continuous IP-binding
enforcement or a zero-leak guarantee. Automatic writes can restart the core
and all connected Marzban nodes. Start with a manual preset.

## Install

On an Ubuntu/Debian host, install the ordinary prerequisites through your
approved package repositories (for example `python3`, `git`, and `cron`). Install
Docker Engine/Compose using [Docker's maintained installation guide](https://docs.docker.com/engine/install/).
The scripts check Docker access and Compose version and then stop if a
dependency is missing. They do not run downloaded installer scripts as root.
Use `sudo` consistently if your Docker setup requires it. Docker access grants
powerful host privileges; keep the checkout and snapshots owned by the trusted
operator and inaccessible to other users.

```bash
git clone https://github.com/linrdev/marzban-residentialip-plugin.git
cd marzban-residentialip-plugin
bash wizard.sh
bash installer.sh --dry-run
bash installer.sh
```

For a new staging stack:

```bash
bash wizard.sh --preset stack-lab --non-interactive --yes
bash intaller.sh --yes
```

The new-stack wizard creates the Marzban sudo password, admin key, and billing
key privately. Read the generated `.env` locally when you need the panel login.
For an existing Marzban, supply its actual credentials and HTTPS management
URL; wizard presets do not bypass certificate verification. For MySQL, enter a
live read-only URI, with URL-encoded credentials. Provider credentials are
optional during installation, but a verified exit cannot be allocated until
the corresponding credential variables are configured.

For automation, populate the existing `MARZBAN_*`, `PROXY_ONE_*`, `PANEL_DOMAIN`,
`VPN_DOMAIN`, and `ACME_EMAIL` environment variables using your secrets manager,
then use `--preset ... --non-interactive --yes`. Do not put passwords in command
arguments or shell history. The generated env file uses literal single quotes;
for Kubernetes, create a separate **unquoted key=value** Secret input as
explained in [deployment.md](../deployment.md).

Generated `.env`, `.resip-admin.json`, and optional `compose.admin.yaml` are
mode `0600` and ignored by Git. Previous wizard configurations are kept under
private `.resip/config-history/`; these are configuration copies, not complete
state snapshots. Snapshots and lock files live under private `.resip/`.
Changing a preset on a running installation does not migrate its data. Preserve
the original project name, snapshot first, and plan any SQLite/MySQL or
existing/bundled topology change as a separate migration.

The installer saves an `installed-baseline` snapshot after startup. On a fresh
installation with no paid leases, this captures the original core before
residential routing is applied. Check that the chosen baseline contains no
`RIP_` routes before using it for full external-addon removal. Imported provider
endpoints and billing grants remain explicit API operations.

## Updater and cron

```bash
bash updater.sh --check
bash updater.sh --apply --yes
# Optional reviewed branch/tag/commit already available from the remote:
bash updater.sh --apply --ref origin/main --yes
bash updater.sh --snapshot-only --include-images --yes
bash updater.sh --cron-example
```

An apply fetches the remote, requires a clean checkout and fast-forward history,
captures a private snapshot, rebuilds the image, starts the chosen Compose
project, and checks local readiness. Local changes or diverging commits are
refused. `--ref` must resolve to a commit available in the checkout. A tag that
would rewind history is not applied as an update; use snapshot restoration for
rollback. The updater does not silently apply security patches to host Linux,
Docker, Marzban/Xray, or a provider's gateway. Check those separately.

If an upgrade fails, the controller is stopped and the script returns nonzero
with an explicit restore command. It does not silently rewind SQLite and lose
new billing events. Existing Xray routes remain on the data plane. Pause
billing delivery and schedule a maintenance window before applying or restoring.
Use a stable local image/registry digest; `--include-images` adds a Docker image
archive so snapshots remain restorable after cache pruning. It may consume
several GB and takes longer. Without it, saved image IDs must remain installed.

Cron defaults should **check** updates and take off-hours snapshots. Add these
manually with `crontab -e` for the trusted deployment owner. Adjust `/opt/...`
and log locations to your actual install. A user's crontab has no username
column; `/etc/cron.d` entries require a username column such as `root`.

```cron
SHELL=/bin/bash
PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin
# Daily update check; deploy only after review.
17 4 * * * cd /opt/marzban-residentialip-plugin && /bin/bash updater.sh --check >> /var/log/resip-update.log 2>&1
# Weekly state snapshot; briefly pauses the controller API.
37 3 * * 0 cd /opt/marzban-residentialip-plugin && /bin/bash updater.sh --snapshot-only --yes >> /var/log/resip-backup.log 2>&1
```

Create logs owned by the deployment user with mode `0600`, configure logrotate,
and monitor nonzero cron exits. Keep sufficient free disk space, encrypt and
copy snapshots off-host, and review retention manually; no snapshot-pruning
cron is installed. A successful update check does not prove image freshness,
traffic health, or completion of a deployment. A weekly snapshot without an
image archive relies on cached image bytes remaining available.

## Snapshots and restore

State snapshots briefly stop the **controller only**, use SQLite's backup API,
pair it with the Marzban core config, and restart the controller if it was
running. Xray remains running. Billing must retry during the pause; avoid
concurrent Marzban admin/core edits while snapshotting. The resulting manifest
records the Git commit, project/preset, installed image IDs, and SHA-256 checksums.
Checksums detect corruption; they are not signed authenticity guarantees.

| Snapshot component | Existing Marzban | Bundled stack |
|---|---|---|
| Controller database, env, Compose/admin configuration, core config | Included | Included |
| Installed image IDs | Included | Included |
| Image bytes | Optional `--include-images` | Optional `--include-images` |
| Marzban SQLite database | External system: untouched, use its own backups | Included |
| Caddy certificate/config volumes | Not managed | Included for `stack-tls` |
| External SQL/provider accounts, host firewall, remote nodes | Not managed | Not managed |

For a restore into this checkout:

```bash
bash remove.sh --restore .resip/snapshots/SNAPSHOT_NAME --dry-run
bash remove.sh --restore .resip/snapshots/SNAPSHOT_NAME --yes
# Also switch a clean Git checkout to the recorded commit:
bash remove.sh --restore .resip/snapshots/SNAPSHOT_NAME --restore-code --yes
```

Restore verifies required files, checksums, SQLite integrity, project identity,
and image availability **before** stopping an existing deployment. It makes a
before-restore snapshot when a current deployment exists, stops the managed
services, restores databases/config/core and optionally image bytes, then starts
the saved images without building or pulling. It works after a purge and
configuration deletion when you still have this source checkout and the private
snapshot. To restore on a replacement host, copy the snapshot and Git history,
install prerequisites, and use an image archive or import the recorded images.

**Auto-apply is disabled after restore.** Review the restored assignments and
Marzban dry-run, reconcile any billing events after the snapshot, and test actual
egress before re-enabling it. Restoring core settings restarts Marzban and its
connected nodes. Standard egress may resume for users absent from an older
snapshot, so disable affected subscribers or isolate traffic during restoration.
`--restore-code` uses a detached checkout and refuses dirty code; return to your
chosen branch deliberately after validating the rollback.

Snapshots contain admin and provider secrets, customer leases, and certificate
private keys. Keep `.resip/` private, preserve it when deleting an installation,
and encrypt off-host copies. Do not commit/upload these snapshots to the public
repository. A retained snapshot is not an erased copy of your secrets.

## Removal / full purge

```bash
# Snapshot, stop, remove managed containers/networks; keep data volumes.
bash remove.sh --yes

# External Marzban: restore its pre-addon baseline, purge controller volume/config.
# Disable premium subscribers first if restoration returns them to standard egress.
bash remove.sh --purge --delete-config \
  --restore-core .resip/snapshots/BASELINE_NAME --include-images --yes

# Bundled stack: purge ALL its volumes, including Marzban users and Caddy state.
bash remove.sh --purge --delete-config --include-images --yes
```

Default removal preserves state and leaves applied `RIP_` routes in Marzban.
An external-addon purge requires an explicitly selected baseline **without**
`RIP_` routes, avoiding orphaned managed routes after controller-state deletion.
Revoke/reconcile paid leases and disable affected users before restoring that
baseline. If snapshot/core restoration fails, removal stops and does not delete
volumes. A bundled purge intentionally deletes this Compose project's Marzban
database and TLS state; the private snapshot is retained for restoration.

Full runtime removal includes only the selected Compose project's containers,
networks and, with `--purge`, volumes. `--delete-config` removes its generated
configuration. Source, snapshots, shared Docker images, host packages,
manually installed cron entries, and external Marzban/SQL/provider resources are
preserved. Remove your cron entries and image caches separately after confirming
no other project uses them. No `rm -rf` of host directories or shared resources
is performed.
