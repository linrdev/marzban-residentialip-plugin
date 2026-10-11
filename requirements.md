# MVP requirements and acceptance criteria

## In scope

- Dockerized Python 3.12 FastAPI controller plus persistent SQLite, single instance.
- External customer billing adapter with signed 5-minute HMAC request and event-ID idempotency; paid entitlement can grant, renew and revoke.
- Validate Marzban username and status using the current REST API, then resolve numeric `users.id` using read-only SQL (public UserResponse does not expose ID). SQLite local read-only mount, or MySQL with least-privilege SELECT account.
- User match uses exact Xray identity `id.username`; never trust client-supplied Xray email.
- Import approved exclusive fixed-IP SOCKS5 endpoints, provider/port/country/credential references; check actual expected exit IP before allocation.
- Atomic 1:1 leasing and release only after confirmed Marzban core update; no two active subscriptions share one exit.
- Generate idempotent managed Xray rules/outbounds preserving unmanaged non-premium config; disallow UDP for premium users; fail close to blackhole on verified probe failure.
- Dry run, apply, return status, every-minute reconciliation (optional), docs and offline tests.
- Provider-neutral import of fixed endpoints, four gateway profiles for discovery, IPRoyal API v1 read-only entry-node discovery.

## Acceptance gates (integration lab required)

1. Import two fixed SOCKS5 exits, verify exact IPs across at least two external IP services, and confirm upstream provider fixed mapping.
2. Create two active Marzban users; billing grant events assign distinct exits. Xray inbound traffic sees each expected public IP; standard user remains on existing DIRECT or configured route.
3. Send duplicate grant webhook; same lease and only one configuration update. Send same event ID different payload; HTTP 409.
4. Disable proxy or change observed IP; controller detects within poll/probe window, blackholes user, and never falls back to a datacenter outbound.
5. Revoke; exit is not reallocated until Xray config successfully stops routing previous user to it. Test PUT failure and retry behavior.
6. Ensure paid_until past UTC triggers removal at next reconciliation and next subscriber can use freed exit after apply.
7. Test DNS, IPv4, IPv6, WebRTC and UDP from actual client apps with packet captures on VPN exit node. UDP should fail, not egress datacenter.
8. Test Marzban multi-node restart behavior; ensure all nodes can reach the same upstream gateway and management API.
9. Remove addon process temporarily: verify existing applied core rules behave as expected; maintain external watchdog for stuck pending/blocked entitlements.
10. Confirm upstream provider permits resale and exclusive allocation for every IP.

## Next increments

- P1: dedicated premium node(s) + network namespaces and firewall egress ACL, WireGuard relay for provider gateway; fail-closed independent of Marzban API.
- P1: Stripe verified payment processor and CoinPayments matching order/settlement verification adapter; price/usage ledger, renewal handling and invoice reconciliation.
- P1: key/secret manager, external dashboard and alerting, multi-node canary deploy/rollback, independent end-to-end tests with Xray.
- P2: PostgreSQL transactional allocation, durable reconcile queue, vendor `list/provision/revoke` procurement adapters (supplier APIs differ), quota/region choice, bulk import, customer portal.
- P2: owned home WireGuard residential gateways for UDP/full-device egress; dual-stack IPv6 and NAT/firewall policies.
