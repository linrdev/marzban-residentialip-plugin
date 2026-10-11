# Deploy the Marzban residential-IP MVP

The controller leases **one verified, exclusive SOCKS5 exit per paid Marzban
user** and applies Xray rules matching the actual `id.username` identity. This
repository contains the restored MVP source, Docker image recipe, Compose
recipes, and two Helm charts. Use a staging user before enabling automatic
configuration updates. See [security constraints](SECURITY.md) and
[acceptance criteria](requirements.md).

For the guided Linux workflow, use `bash wizard.sh` and `bash installer.sh`.
See the [Linux admin manual](docs/linux-admin.md) for eight presets, update/cron
commands, private snapshots, removal, and complete restoration.

## Choose a deployment

| Recipe | Use it for | Persistent state | API access |
|---|---|---|---|
| `compose.yaml` | Existing Marzban on this Linux host | Add-on volume; read-only live Marzban SQLite directory | Loopback `8787`; Marzban API over HTTPS |
| `compose.yaml` + `compose.mysql.yaml` | Existing remote Marzban with MySQL | Add-on volume; restricted SQL identity account | Loopback `8787`; HTTPS management API |
| `deploy/compose.stack.yaml` | New single-host Marzban + controller | Separate Marzban and add-on volumes | Both admin APIs on loopback; optional Caddy TLS |
| `charts/residential-egress` | Existing Marzban from Kubernetes | One add-on PVC; live external SQL or identity PVC | ClusterIP; optional NetworkPolicy |
| `charts/marzban-residential-stack` | New Kubernetes staging stack | Separate PVCs; both processes in one pod | Private APIs; optional VLESS TLS Ingress |

**One replica, one worker, one controller per Marzban core configuration.**
The add-on state database is SQLite. Helm uses `Recreate` and rejects replicas
other than one. Do not deploy two releases against the same Marzban or use
`docker compose --scale`. Upgrades deliberately cause controller downtime;
bundled Helm upgrades also restart Marzban. Existing Xray rules persist while
the controller is stopped.

## Requirements

| Component | Requirement |
|---|---|
| Controller | Linux amd64/arm64; Python 3.12 image; estimate 1 vCPU, 512 MB RAM, 2 GB state disk |
| New full stack | Start with 2 vCPU, 2–4 GB RAM, 10 GB disk; proxy throughput determines additional capacity |
| Compose | Docker Engine with Compose **2.24.4+**; v1 `docker-compose` is unsupported |
| Kubernetes | **1.27+**, Helm **3.13+**, kubectl, a filesystem PVC StorageClass with POSIX permissions and SQLite locking |
| PVC isolation | Prefer `ReadWriteOncePod` on Kubernetes **1.29+** with compatible CSI; `ReadWriteOnce` does not enforce one pod |
| TLS Ingress | Installed WebSocket-capable ingress controller; existing TLS Secret or configured cert-manager issuer |
| Marzban API | Sudo service account; token, user, GET/PUT core-config endpoints; verified HTTPS for remote API |
| Identity source | Live Marzban SQL `users(id,username)`; read-only SQLite directory or SQL account limited to those columns |
| Provider | Purchased fixed exclusive SOCKS5 mapping, expected public IP, provider permission to resell/VPN-use |
| Networking | Controller **and every Xray node** can reach the proxy gateway; encrypt public SOCKS5 transport separately |

The chart validates Kubernetes API compatibility, not the support lifecycle of
your cluster. Use a maintained Kubernetes release. The add-on image can be
built for amd64/arm64; verify the chosen upstream Marzban image has your target
platform before installing. Avoid NFS/shared storage that does not provide
reliable SQLite locking. The bundled Marzban container runs as root, matching
upstream, so it needs a namespace policy permitting it. Use the standalone
controller chart in a restricted namespace.

## 1. Existing Marzban: Docker Compose

Run from the repository root:

```bash
python3 scripts/init_env.py
# Edit existing Marzban URL/credentials, SQL identity source, provider secrets.
nano .env
docker compose config --quiet
docker compose up -d --build
curl -fsS http://127.0.0.1:8787/readyz
```

`init_env.py` writes mode `0600`, generates distinct admin/HMAC keys, and refuses
to overwrite an existing file. Example credentials are rejected at startup.
Use HTTPS with a valid certificate; there is no TLS-verification bypass.
For a private CA, mount its PEM bundle and set `SSL_CERT_FILE` in the controller.
Only enable `RESIP_ALLOW_HTTP_MARZBAN=true` for a trusted private connection.

The default read-only bind mount is `/var/lib/marzban` to `/marzban-ro`; it
refuses to silently create a missing host directory. UID/GID `10001:10001` must
read the database and existing WAL/SHM files. Grant narrowly scoped read ACLs
or a dedicated group; do not make the entire directory world-readable.
Read-only WAL access can fail if SQLite needs to create missing sidecar files.
Use a restricted live MySQL account when read-only SQLite cannot work safely.
Never use `immutable=1` on a live changing identity database.

For MySQL, set `MARZBAN_DB_URL` in `.env` and remove the SQLite mount with the
provided override:

```bash
docker compose -f compose.yaml -f compose.mysql.yaml config --quiet
docker compose -f compose.yaml -f compose.mysql.yaml up -d --build
```

Example grant executed by your database administrator, **after** the Marzban
schema exists (replace user, host, and password):

```sql
CREATE USER 'resip_identity'@'CONTROLLER_PRIVATE_IP' IDENTIFIED BY 'STRONG_PASSWORD';
GRANT SELECT (id, username) ON marzban.users TO 'resip_identity'@'CONTROLLER_PRIVATE_IP';
```

Use `mysql+pymysql://resip_identity:URL_ENCODED_PASSWORD@DB_HOST:3306/marzban`.
URL-encode username/password components. Use a private encrypted link or
driver-supported verified TLS for SQL; the URI alone does not enable SQL TLS.
The identity account must read the same live database used by Marzban.

## 2. New Marzban: complete Compose stack

Use a separate host or Compose project from an existing installation. This
recipe does not migrate or overwrite existing Marzban data.

```bash
python3 scripts/init_env.py --new-marzban
# Set real proxy credentials; set PANEL_DOMAIN, VPN_DOMAIN, ACME_EMAIL.
nano .env
docker compose --env-file .env -f deploy/compose.stack.yaml config --quiet
docker compose --env-file .env -f deploy/compose.stack.yaml up -d --build
curl -fsS http://127.0.0.1:8787/readyz
```

The seed VLESS inbound listens on private container port `1080`, WebSocket path
`/vless-ws`; it is not published as plaintext on the host. The Marzban panel
is on `127.0.0.1:8000`. Use an SSH tunnel for staging:

```bash
ssh -L 8000:127.0.0.1:8000 -L 8787:127.0.0.1:8787 admin@SERVER
```

Open `http://127.0.0.1:8000/dashboard/` locally. Credentials are the
`MARZBAN_SUDO_USER` / generated `MARZBAN_SUDO_PASSWORD` in `.env`. This new
deployment bootstraps an upstream sudo account using `SUDO_USERNAME/PASSWORD`.
For an established deployment, create a proper upstream sudo admin instead.

For public TLS, point both DNS names to the server, allow TCP `80/443`, configure
management access restrictions for the panel host, and then start Caddy:

```bash
docker compose --env-file .env -f deploy/compose.stack.yaml --profile tls up -d --build
```

Caddy obtains and renews certificates. Its two routes are
`https://PANEL_DOMAIN` → Marzban and `https://VPN_DOMAIN/vless-ws` → VLESS.
**The panel route becomes public when this profile is enabled**; place it
behind your management access proxy/VPN or configure an explicit IP allowlist.
The controller has no public Caddy route. Its HMAC endpoint is internal only.
For panel isolation without the public panel route, remove that site block
from `deploy/Caddyfile` and retain loopback/SSH management.

In Marzban's host settings for `VLESS_WS`, set address/SNI to `VPN_DOMAIN`,
public port `443`, WebSocket path `/vless-ws`, and TLS enabled. Marzban's seed
inbound is plaintext **inside the trusted host bridge**, behind Caddy TLS.
Configure the subscription prefix for your panel/subscription endpoint.
Create an active staging user through Marzban before sending a grant.

The Xray seed is copied only on first startup; editing the seed does not replace
live config. Change live settings through Marzban and back them up. `v0.8.4`
is the release tag reported by upstream during implementation; pin tested
image digests before a real rollout. A release tag is not a security audit.

## 3. Kubernetes / Helm

Build and publish your image; no prebuilt registry image is claimed:

```bash
docker build -t REGISTRY/linrdev/marzban-residentialip-plugin:0.2.0 .
docker push REGISTRY/linrdev/marzban-residentialip-plugin:0.2.0
# For heterogeneous nodes, use buildx --platform linux/amd64,linux/arm64 --push.
```

For a local k3d/kind lab, build the same image and import it into that cluster
instead of pushing. The chart defaults to `IfNotPresent`; cluster nodes still
need the image. Prefer `image.digest` for reproducible deployments.

Prepare a namespace and Secret from a **private, edited, unquoted key=value**
env file. Kubernetes' env-file parser does not implement Docker's dotenv
interpolation/quote removal. Do not pass credentials through `--set` or values
files, which persist in Helm release metadata.

```bash
kubectl create namespace resip
kubectl -n resip create secret generic resip-secrets --from-env-file=.env
```

Required Secret keys: `RESIP_API_KEY`, `RESIP_BILLING_HMAC_KEY`,
`MARZBAN_SUDO_USER`, `MARZBAN_SUDO_PASSWORD`, and every upstream credential
variable referenced by imported exits. Standalone installs also need
`MARZBAN_DB_URL` unless a SQLite URI is set in chart configuration. Protect
Secret RBAC and enable cluster encryption at rest or your external secret
manager; Kubernetes Secrets are not encrypted by base64 encoding.

### Existing Marzban

Edit `deploy/helm-existing.example.yaml`: registry, Marzban HTTPS URL, storage
class, and optional existing claims. Put the restricted MySQL URI in the
Secret. Then:

```bash
helm lint charts/residential-egress
helm upgrade --install resip charts/residential-egress -n resip \
  -f deploy/helm-existing.example.yaml --wait --timeout 5m
kubectl -n resip port-forward svc/resip-resip 8787:8787
```

For an existing live SQLite PVC, set `identity.existingClaim`, use the mounted
`/marzban-ro/db.sqlite3` URI, and configure affinity to its owner node when
required by that volume. A `ReadWriteOncePod` identity PVC cannot be attached
to two separate pods. The chart cannot grant permissions or solve cross-node
WAL coherence; a remote SQL lookup is usually simpler.

### New bundled stack

The stack chart packages the controller chart with `marzban.enabled=true`.
Both containers share one pod, so the controller can read Marzban's live
SQLite volume without copying identities or cross-node mounts. Their state
PVCs remain separate. Both APIs are ClusterIP/private.

```bash
helm dependency build charts/marzban-residential-stack
helm lint charts/marzban-residential-stack
# Replace registry in an example copy; disable ingress for a private staging lab.
helm upgrade --install resip charts/marzban-residential-stack -n resip \
  -f deploy/helm-stack.example.yaml --wait --timeout 5m
kubectl -n resip port-forward svc/resip-resip-marzban 8000:8000
```

The stack example enables TLS ingress for `/vless-ws` only. It assumes an
installed `nginx` ingress class in namespace `ingress-nginx` and a configured
cert-manager `letsencrypt-prod` ClusterIssuer. Change these to match your
cluster, or precreate the `vpn-tls` Secret and remove the issuer annotation.
The charts install neither an ingress controller nor cert-manager. Set
Marzban's external host settings to your actual VPN hostname, `443`, TLS and
`/vless-ws`; the chart does not automate subscription-host customization.

The default NetworkPolicy allows controller callers with
`resip-access=true` **in the same namespace**. Label the billing pod template
accordingly. The stack example additionally allows ingress-controller peers
on `1080`; panel `8000` stays private. The CNI must enforce NetworkPolicy.
Egress is unrestricted by default; if you set `networkPolicy.egress`, allow
DNS, Marzban API/SQL and proxy endpoints. In the bundled pod, the policy also
affects Xray subscriber traffic, so assess standard-account routing too.

Use `kubectl port-forward` for local admin access. Protect remote billing
connections with your private authenticated network; a ClusterIP is not mTLS.
After changing an existing Secret, restart the deployment to reload env:

```bash
kubectl -n resip rollout restart deployment/resip-resip
kubectl -n resip rollout status deployment/resip-resip
```

The restart also restarts Marzban in the bundled stack. No HPA, replica scaling,
public controller Ingress, or automatic live deployment is configured.

## 4. Verify, allocate, and apply

1. Replace placeholder IP/host/port in `examples/generic-dedicated-residential.json`
   or `examples/dedicated-oxylabs.json`. Documentation IP ranges are refused.
2. Import with `X-Admin-Key`; POST `/v1/exits/{id}/verify` must observe the exact
   purchased address. Verify independently from **each actual Xray node** too.
3. Update `examples/billing-grant.json` with an active staging username and a
   future UTC `paid_until` Unix timestamp. Have your verified billing backend
   sign the event. For the local lab, `scripts/send_event.py` reads the signing
   key from its process environment; export it securely, not into shell history.
4. POST `/v1/reconcile?dry_run=true` and review `/v1/status`.
5. Back up the original Marzban core config, then POST
   `/v1/reconcile?dry_run=false` during a maintenance window. This restarts
   the core **and all connected nodes**; it is not a per-user hot reload.
6. Test actual client egress, DNS/IPv6/WebRTC, a dead proxy, expiry and revoke.
   Premium UDP/QUIC is blackholed in this MVP. Existing TCP sessions may
   require reconnecting after a route update; verify revocation behavior live.

See [README API examples](README.md#one-subscriber-one-dedicated-ip).
Enable `RESIP_AUTO_APPLY=true` / Helm `config.autoApply=true` only after the lab
acceptance checks pass. Automatic reconcile polls every 60 seconds by default;
active exits are re-probed after half `RESIP_PROBE_MAX_AGE` (default ~30 minutes).
It restarts Marzban only when the desired configuration changes.

## 5. Monitoring, backups, upgrades, and rollback

| Check | Interpretation / response |
|---|---|
| `/healthz` | Process liveness only |
| `/readyz` | Local state database is readable; not upstream traffic health |
| Authenticated `/v1/status` | Alert on unhealthy, pending/revoking, blocked premium, or required changes |
| `/v1/exits` | Check probe age/observed IP; exact IP must match purchased address |
| Actual subscriber probes | Detect routing/DNS/IPv6 problems that controller probes cannot see |

Back up controller SQLite using its online backup API. For default Compose:

```bash
mkdir -p backups
bash scripts/backup.sh backups/resip.db
```

For the stack, use `bash scripts/backup.sh --stack backups/resip.db`.
For Kubernetes:

```bash
kubectl -n resip exec deployment/resip-resip -c residential-addon -- python -c \
  'import sqlite3,base64,tempfile,pathlib; s=sqlite3.connect("/data/resip.db"); f=tempfile.NamedTemporaryFile(); d=sqlite3.connect(f.name); s.backup(d); d.close(); s.close(); print(base64.b64encode(pathlib.Path(f.name).read_bytes()).decode())' \
  | base64 -d > backups/resip.db
chmod 600 backups/resip.db
```

Also back up Marzban SQL, its writable Xray config, and private configuration
using upstream's backup procedure. Core config contains proxy credentials.
Keep encrypted off-host backups and test restoration. Do not copy a changing
SQLite database with a plain file copy.

Upgrade by recording current image digests and Helm revision, backing up state,
testing the new image in staging, then rebuilding Compose or running
`helm upgrade --wait`. Helm `Recreate` stops the old pod before starting the
replacement. Auto-apply should be disabled during controller/Marzban upgrades.
`helm rollback resip REVISION -n resip --wait` restores manifests/images, **not**
SQLite contents or the Marzban core configuration. Check schema compatibility
before rolling back application versions.

To restore controller state, stop the sole controller first; copy the verified
backup into `/data/resip.db` through a maintenance container/pod mounting its
volume, set owner/group `10001`, then restart and dry-run. Reconcile the
restored leases with the backed-up Marzban core before writing. Never restore
a DB while the controller is running.

PVCs carry `helm.sh/resource-policy: keep`, so uninstall retains data. When
reinstalling, set the retained claims as `persistence.existingClaim` and
`marzban.persistence.existingClaim`; do not rely on automatic re-adoption.
Compose `down` retains volumes; **`down -v` deletes state**. Stopping or
uninstalling the controller does not remove already-applied `RIP_` routes.
For a planned removal, revoke/reconcile leases first. Emergency core-config
restoration can return premium users to standard routing, so disconnect or
disable those users before removing their residential rules.

## Validation and implementation boundaries

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements-dev.txt
.venv/bin/pytest -q
.venv/bin/python scripts/validate_infra.py
```

The infrastructure validator needs Helm and the Compose v2 CLI (or
`HELM_BINARY` / `COMPOSE_BINARY`). It renders both charts, verifies the Compose
recipes, and checks single-instance/private-network/storage assumptions. See
[validation notes](docs/validation.md) for results and remaining live gates.
No server/cluster is provisioned by this commit, and no image is pre-published.
Offline tests and manifest validation cannot prove live Marzban/Xray/provider
behavior. Stripe/CoinPayments signature verification remains the responsibility
of your billing backend; this add-on accepts only its signed entitlement events.

## Primary references

- [Marzban core configuration API and restart implementation](https://github.com/Gozargah/Marzban/blob/master/app/routers/core.py)
- [Marzban Xray identity injection](https://github.com/Gozargah/Marzban/blob/master/app/xray/config.py)
- [Marzban v0.8.4 release](https://github.com/Gozargah/Marzban/releases/tag/v0.8.4)
- [Xray routing rules and user matches](https://xtls.github.io/en/config/routing.html)
- [Docker Compose merge and override semantics](https://docs.docker.com/reference/compose-file/merge/)
- [Kubernetes persistent volume access modes](https://kubernetes.io/docs/concepts/storage/persistent-volumes/#access-modes)
- [Helm chart values schemas](https://helm.sh/docs/topics/charts/#schema-files)

Sources were checked during implementation; review upstream compatibility again
before selecting real deployment image digests.
