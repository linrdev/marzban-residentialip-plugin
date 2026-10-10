# Marzban Residential Egress Add-on — MVP v0.1

**An independent, single-node control-plane for assigning verified, exclusive residential/ISP SOCKS5 exits to paid Marzban users.** Does not fork Marzban, change subscriber credentials, or install another VPN on subscriber devices.

**Status:** Runnable reference MVP with mocked Marzban and upstream tests. **NOT production-certified**; no real provider credentials, live Marzban nodes or Xray binary were available for integration testing. See `SECURITY.md` and the rollout checklist before selling this feature.

## Upstream projects and acknowledgements

This add-on was developed as an **independent integration project**, not as an official Marzban module. It depends on the APIs, public documentation, and open-source infrastructure built and maintained by others. Thanks to:

- **[Marzban](https://github.com/Gozargah/Marzban)** by **[Gozargah](https://github.com/Gozargah)** and its [contributors](https://github.com/Gozargah/Marzban/graphs/contributors): user management, authenticated identity model, and Xray configuration API. Official source: `Gozargah/Marzban` (default branch `master`).
- **[Marzban-node](https://github.com/Gozargah/Marzban-node)** and its [contributors](https://github.com/Gozargah/Marzban-node/graphs/contributors): the upstream distributed-node component and reference for multi-node operations. This add-on does **not** implement node internals.
- **[Xray-core](https://github.com/XTLS/Xray-core)** by the **[XTLS community](https://github.com/XTLS)** and its [contributors](https://github.com/XTLS/Xray-core/graphs/contributors): SOCKS outbounds, user-based routing, and the fail-closed configuration primitives. [Xray configuration documentation](https://xtls.github.io/en/config/).
- The maintainers and contributors of **[FastAPI](https://github.com/fastapi/fastapi)**, **[Pydantic](https://github.com/pydantic/pydantic)**, **[Uvicorn](https://github.com/encode/uvicorn)**, **[HTTPX](https://github.com/encode/httpx)**, **[SQLAlchemy](https://github.com/sqlalchemy/sqlalchemy)**, **[PyMySQL](https://github.com/PyMySQL/PyMySQL)**, and **[pytest](https://github.com/pytest-dev/pytest)**: the Python application, validation, connectivity, persistence, and test ecosystem.

Provider-specific topology examples reference publicly documented endpoints from **[Oxylabs](https://developers.oxylabs.io/)**, **[Bright Data](https://docs.brightdata.com/)**, **[Decodo](https://help.decodo.com/)**, and **[IPRoyal](https://docs.iproyal.com/)**. These companies are *documentation sources*, not project sponsors or contributors. See [`docs/provider-topology.md`](docs/provider-topology.md) for exact links and implementation boundaries.

**Attribution is not endorsement.** No partnership or official affiliation with Marzban, Gozargah, XTLS, or any residential provider is claimed. Third-party code and packages remain under their respective licenses; the original add-on code is distributed under [MIT](LICENSE). If you build on these foundations, please retain relevant upstream notices and acknowledge upstream maintainers.

## GitHub publishing / upstream fork

This repository is intended to live at **[`linrdev/marzban-residentialip-plugin`](https://github.com/linrdev/marzban-residentialip-plugin)**. The matching Marzban fork should be created using GitHub's **Fork** function for [the original `Gozargah/Marzban`](https://github.com/Gozargah/Marzban), to preserve GitHub's fork relationship and attribution. **Do not** push an unrelated copied tree as if it were a fork.

Once GitHub CLI is installed and authenticated as `linrdev`, run `bash scripts/publish-github.sh` from a clean Git checkout. It creates the public add-on repository (or safely pushes to an existing empty repository) and forks the current upstream `Gozargah/Marzban` into `linrdev/Marzban` if no fork already exists. It refuses to overwrite a nonempty or conflicting destination. The script requires network access and suitable GitHub repository-create/fork permissions.

## Architecture

```text
Stripe/CoinPayments → your billing backend (validates actual payment)
                                  │ signed HMAC entitlement event
                                  ▼
   Residential add-on :8787 ── SQLite (one active instance)
      ├── Provider-independent SOCKS5 exit inventory
      ├── Provider gateway examples + optional IPRoyal v1 discovery API
      ├── Probe actual IP / reserve once / renew / revoke
      ├── Marzban REST: token, GET user, GET+PUT core config
      └── Marzban SQL: read-only SELECT users.id by username
                                         │ Xray core restart
                                         ▼
Marzban subscriber → Xray authenticated user email → per-user routing
       ├── Standard account → pre-existing routing untouched
       └── Paid active → dedicated SOCKS5 upstream → expected public IP
                          (UDP explicitly blackholed in MVP)
```

**Semantics:** paid `+ private residential IP` is *not* a rotating or time-limited sticky session. Register only dedicated, exclusive IP/port mappings under a valid resale license, and independently test expected egress IP. An ISP-assigned proxy is not automatically a real household connection. Advertise the accurate address class.

## Host prerequisites

- Docker Engine and Docker Compose v2, Linux x86_64/ARM64, minimum **1 vCPU, 512 MB RAM, 2 GB disk** for this small add-on (excluding existing Marzban and upstream gateways).
- Working HTTPS reachability to Marzban's superuser REST API, with GET `/api/user/{username}`, GET/PUT `/api/core/config`, POST `/api/admin/token`.
- **Read-only access to Marzban's identity SQL database.** The REST `UserResponse` omits numeric `id`, yet Xray uses `id.username`. Default Marzban SQLite installs normally store `/var/lib/marzban/db.sqlite3`, mounted read-only by `compose.yaml`; MySQL users can set `MARZBAN_DB_URL=mysql+pymysql://...` using an account restricted to `SELECT id,username FROM users` and remove the SQLite volume. For SQLite, confirm container UID `10001` can read the file and its WAL/SHM files, if used.
- Reliable tunnel/private routing to upstream SOCKS5 proxy gateway (raw SOCKS5 is **not encrypted**).
- One purchased *exclusively assigned* upstream SOCKS5 proxy, expected fixed egress IP, and permission from provider for resale / subscriber VPN use.
- Controlled DNS/full-tunnel VPN client policy. This server add-on cannot itself prevent every client-side leak.

## Quick start (Ubuntu 24.04 / other Linux with Docker)

```bash
cp .env.example .env
chmod 600 .env
python3 - <<'PY'
import secrets
p='.env'
s=open(p).read().replace('REPLACE_WITH_RANDOM_32PLUS_BYTES',secrets.token_urlsafe(48),1)
s=s.replace('REPLACE_WITH_DIFFERENT_RANDOM_32PLUS_BYTES',secrets.token_urlsafe(48),1)
open(p,'w').write(s)
PY
# Edit MARZBAN_URL, MARZBAN_SUDO_USER, MARZBAN_SUDO_PASSWORD,
# PROXY_ONE_USERNAME and PROXY_ONE_PASSWORD, and check
# MARZBAN_DB_URL / MARZBAN_DATA_DIR (SQLite) before continuing:
nano .env

docker compose up -d --build
curl -fsS http://127.0.0.1:8787/healthz
```

**Only loopback is published:** `127.0.0.1:8787`. Do not expose to the public Internet or ship the env file to a customer. Use an internal reverse proxy/mTLS for an external billing backend. The `healthz` endpoint is a liveness check, **not** a guarantee that Marzban or proxy exits are healthy.

## One subscriber, one dedicated IP

1. Edit `examples/dedicated-oxylabs.json` to replace the *documentation-only* expected IP `203.0.113.10` and actual provider-supplied gateway hostname/port. For Oxylabs dedicated ISP self-service, `8001`, `8002`, etc. map to fixed IPs; `8000` is rotating and is refused.
2. Admin key only from local shell: `set -a; source .env; set +a` (quote secrets containing shell metacharacters). Preferred: export secrets from a trusted secrets manager rather than `source`.
3. Import and verify a dedicated exit:

```bash
curl -fsS -X POST http://127.0.0.1:8787/v1/exits \
  -H "X-Admin-Key: $RESIP_API_KEY" -H 'Content-Type: application/json' \
  --data-binary @examples/dedicated-oxylabs.json
curl -fsS -X POST http://127.0.0.1:8787/v1/exits/de01/verify \
  -H "X-Admin-Key: $RESIP_API_KEY"
```

4. Only your billing backend should sign events **after** verified paid/captured status. For local test, update `examples/billing-grant.json` with a real active Marzban user and future `paid_until` UTC Unix seconds:

```bash
python3 scripts/send_event.py examples/billing-grant.json
curl -fsS -X POST 'http://127.0.0.1:8787/v1/reconcile?dry_run=true' \
  -H "X-Admin-Key: $RESIP_API_KEY"
# WARNING: writes core config and restarts Marzban core + all connected nodes
curl -fsS -X POST 'http://127.0.0.1:8787/v1/reconcile?dry_run=false' \
  -H "X-Admin-Key: $RESIP_API_KEY"
curl -fsS http://127.0.0.1:8787/v1/assignments -H "X-Admin-Key: $RESIP_API_KEY"
```

Set `RESIP_AUTO_APPLY=true` **only after** successfully reviewing manual deployment, DNS policy, upstream tunnel reachability *from every Marzban node*, monitoring and maintenance windows. Auto mode reconciles every 60 seconds and after each signed event. It may repeatedly restart the core **only when desired configuration changes**, not every polling cycle.

> `MARZBAN_URL` is HTTPS by default. For non-public HTTP through a *trusted private network only*, set `RESIP_ALLOW_HTTP_MARZBAN=true`. There is **no** built-in TLS bypass / `verify=False`.

## REST endpoints

OpenAPI: `http://127.0.0.1:8787/docs` (private admin access only).

| Method | Path | Purpose |
|---|---|---|
| GET | `/healthz` | Process liveness |
| POST | `/v1/exits` | Register manually attested fixed SOCKS5 exit, env variable references for secrets |
| GET | `/v1/exits` | IP inventory and allocation state, no passwords |
| POST | `/v1/exits/{id}/verify` | Probe exit through SOCKS5 and check actual exact public IP |
| GET | `/v1/assignments` | Current user-to-exit leases |
| GET | `/v1/status` | Authenticated reconciliation/drift and activation readiness status |
| POST | `/v1/billing/events` | `grant`/`revoke`, HMAC-SHA256 + timestamp + event-ID replay protection |
| POST | `/v1/reconcile?dry_run=true` | Compare computed config without writing Marzban |
| POST | `/v1/reconcile?dry_run=false` | Apply update, verify reread, then activate/release leases |
| GET | `/v1/gateways/{provider}?username=...&session=...` | Non-private session/gateway topology examples |
| GET | `/v1/provider/iproyal/entry-nodes` | Optional *real* IPRoyal REST v1 discovery (`IPROYAL_API_TOKEN`) |

Admin endpoints use `X-Admin-Key`; billing uses **different** HMAC key. HMAC signature is a lowercase SHA-256 hex digest of `<timestamp>.<raw JSON bytes>`; send `X-Resip-Timestamp`, `X-Resip-Signature`, `Content-Type: application/json`. `event_id` and raw payload digest are persisted; changing a previously used event ID returns 409. Expired timestamps (5 minutes) return 401.

No direct Stripe/CoinPayments hook is exposed. **Your billing service** must verify the *real* provider-specific webhook before translating it into an internal signed entitlement event. Never trust customer-facing checkout status or arbitrary `grant` calls.

## Supported provider topology

The **generic fixed SOCKS5 importer works across suppliers**; public vendor gateway examples are separately surfaced for integration tests and future optional rotating product tiers. See `docs/provider-topology.md` for concrete vendor links and guarantees.

| Provider mode | Interface | Private exclusive IP supported? |
|---|---|---|
| Oxylabs dedicated ISP | `disp.oxylabs.io` fixed ports (example 8001) | Yes, if actually purchased and verified |
| Generic dedicated residential or ISP | Supplier-issued host:port plus SOCKS5 auth | Yes, with exclusivity documentation and verified IP |
| Bright Data rotating/sticky residential | `brd.superproxy.io` SOCKS5 gateway | **No**, sticky ≠ exclusive |
| Oxylabs rotating/sticky residential | `pr.oxylabs.io` gateway | **No**, sticky ≠ exclusive |
| Decodo rotating/sticky residential | `gate.decodo.com` gateway | **No**, sticky ≠ exclusive |
| IPRoyal residential | Bearer REST v1 entry-node discovery | **No** proof of dedicated IP from discovery |

Out-of-scope today: purchasing IPs automatically from all suppliers, managing vendor accounts/subusers, WireGuard full UDP egress, traffic-aware billing, automatic multi-node gradual rollout, native Stripe/CoinPayments signature verification, automatic return of replaced IP when suppliers revoke it.

## Tests

```bash
python3 -m venv .venv
. .venv/bin/activate
pip install -r requirements-dev.txt
pytest -q
```

Tests run entirely offline with fake Marzban and proxy probe; a real end-to-end lab gate is required. A signed event example is in `scripts/send_event.py`.

## Rollout gates

- Backup Marzban core settings first. Test `/api/core/config` permissions; confirm the read-only Marzban SQL identity lookup matches the deployed `users(id,username)` schema and that the version still uses `id.username` for Xray `email`.
- Confirm proxy IP **equals the purchased allocation** on at least two independent external IP-test services, and confirm provider terms allow dedicated resale.
- Restrict the add-on network and API; encrypt upstream SOCKS5 connectivity via WireGuard/private link. Marzban API should use verified TLS.
- DNS + IPv4/IPv6 + QUIC + WebRTC tests from actual user clients; the MVP *blocks UDP* for residential subscribers.
- Watch `/v1/status` fields `pending`, `blocked_premium`, `change_required`, and failures; check probe age from `/v1/exits`. Configure external monitoring: healthz alone misses all of these.
- First deploy on one staging Marzban node. Core updates restart **all connected nodes**. During an unknown live outage, an already-applied premium outbound still fails closed to its assigned SOCKS proxy, but no zero-leak hard guarantee exists without separate host-level egress controls.
- Schedule backup of SQLite volume and record Marzban config snapshots. See `scripts/backup.sh`.

**License:** MIT. Not affiliated with Marzban or any proxy provider.