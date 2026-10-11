# Validation record

This implementation was checked on 2026-10-11 UTC using Python 3.12,
Helm 3.17.3, and Compose 2.39.4.

| Check | Result |
|---|---|
| Application and admin unit tests | 45 passed; one upstream TestClient deprecation warning |
| Both Helm charts | Strict lint and template rendering passed |
| Chart variants | Existing identity PVC, retained data PVC, ReadWriteOncePod, bundled Marzban, TLS ingress, and egress policy rendered |
| Unsafe chart values | Multiple replicas, blank Secret name, unapproved external HTTP, invalid poll interval, credentials in values, and incomplete ingress config rejected |
| Compose | Existing SQLite, MySQL mount override, private full stack, and optional Caddy TLS profile validated |
| Bash entrypoints | Syntax/help/dry-run checks passed; executable modes set |
| Documentation | Relative README/deployment/manual links checked |

Regression coverage includes exit reuse after revoke, exclusive allocation,
HMAC/event idempotency, apply failure, stale/failed probes, and webhook
revocation serialized against a core apply. Admin coverage includes literal
secret parsing, private files, operation locking, all eight presets, consistent
snapshot capture, corruption/path rejection, image availability checks before
restore, restoration after configuration deletion, guarded purge, and
check-only updates avoiding Docker deployment.

The admin lifecycle is tested with mocked Docker/Git boundaries. No Docker
daemon, real cluster, running Marzban/Xray, paid provider endpoint, or actual
payment-gateway credentials were available for a live deployment. Images were
not built/pushed and no servers were changed. Render and unit results establish
configuration and control-flow behavior, not end-to-end VPN correctness.

Before using real customers, complete the lab gates in [requirements.md](../requirements.md)
and [deployment.md](../deployment.md), including a real install → grant → apply
→ revoke → update → snapshot → purge → restore cycle. Check current Marzban/Xray
identity/config compatibility, filesystem permissions, every node's proxy
reachability, encrypted upstream transport, client DNS/IPv6/WebRTC behavior,
and billing retry/reconciliation after snapshots. Premium UDP is unsupported.

Run the repeatable checks with:

```bash
.venv/bin/pytest -q
.venv/bin/python scripts/validate_infra.py
bash -n wizard.sh installer.sh intaller.sh updater.sh remove.sh scripts/backup.sh
```

Set `HELM_BINARY` and `COMPOSE_BINARY` when those CLIs are installed outside PATH.
