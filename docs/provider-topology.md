# Provider API and gateway integration matrix (reviewed October 2026)

The add-on normalizes **data-plane** connectivity to `SOCKS5(host, port, username, password)` and keeps **control-plane** discovery/ordering separate. This is an intentional compatibility layer: suppliers disagree on session names, customer accounts, zones, endpoint lists, IP binding, and inventory purchase models. No single supplier API or SOCKS gateway guarantees exclusive ownership.

| Supplier | API / protocol topology | Current MVP integration | Product classification |
|---|---|---|---|
| **Bright Data** | `brd.superproxy.io:22228` SOCKS5; username `brd-customer-...-zone-...` and optional `-session-...` | Gateway profile; fixed dedicated exports can be imported manually | Rotating/sticky residential is NOT private exclusive |
| **Oxylabs Residential** | `pr.oxylabs.io:7777`, username `customer-USER-sessid-ID`, SOCKS5 supported | Gateway profile; no automatic IP reservation | Sticky session is NOT dedicated |
| **Oxylabs Dedicated ISP** | `disp.oxylabs.io:8001` etc. each associated with fixed purchased IP; `8000` rotates | Verified import, exclusive allocator; guard refuses port 8000 | Suitable when contract permits resale; ISP proxy ≠ household peer |
| **Decodo** | `gate.decodo.com:7000`; sticky username `user-NAME-session-ID-sessionduration-30` | Gateway profile for future shared/sticky tier; generic fixed import for paid static ISP | Sticky is NOT exclusive |
| **IPRoyal** | REST v1 `https://resi-api.iproyal.com/v1` Bearer auth; `GET /access/entry-nodes` and `POST /access/generate-proxy-list` | Live entry-node discovery, no vendor credentials returned; manual fixed SOCKS5 import only if exclusive | `generate-proxy-list` sticky/rotating is NOT dedicated |
| **Any other fixed SOCKS5 supplier** | Named/authenticated host:port for unique fixed public IP | Generic importer without provider API credentials | Suitable only if fixed, exclusive and IP-verified |

## Why not auto-purchase from every supplier?

Supplier-specific account/zone purchasing and allocations are not standardized. In addition, rotating gateway usernames are often *sessions*, not owned IPs; their IP can change when peers disconnect. The MVP **never upgrades a sticky gateway into an exclusive entitlement by itself**. Purchase and document an exclusive endpoint through the supplier first, store the credentials as environment secrets, register expected IP, probe it, then make it available to the allocator.

Future provider adapters should implement a contract with functions `list_purchased_fixed_ips()`, `provision_exclusive_egress()`, `check_lease()`, `revoke_lease()` and `build_socks_endpoint()`; separately model `rotation_policy`, `exclusive`, `ip_class`, `supports_udp`, `subuser`, `quota_gb`, `allowed_countries`, `lease_end`.

## Official references

- Marzban core config GET/PUT (core restart semantics): https://github.com/Gozargah/Marzban/blob/master/app/routers/core.py
- Marzban user GET: https://github.com/Gozargah/Marzban/blob/master/app/routers/user.py
- UserResponse omits `id`; DB users table has `id`: https://github.com/Gozargah/Marzban/blob/master/app/models/user.py and https://github.com/Gozargah/Marzban/blob/master/app/db/models.py
- Marzban identity `email=f"{user_id}.{username}"`: https://github.com/Gozargah/Marzban/blob/master/app/xray/config.py
- Xray SOCKS outbound settings, no transport encryption: https://xtls.github.io/en/config/outbounds/socks.html
- Xray user routing rules: https://xtls.github.io/en/config/routing
- Bright Data proxy and SOCKS5 gateway: https://github.com/brightdata/skills/blob/main/skills/proxy/SKILL.md
- Oxylabs residential gateway and `sessid`: https://oxylabs.io/blog/residential-proxies-quick-start-guide
- Oxylabs dedicated ISP port-to-IP binding: https://developers.oxylabs.io/help-center/getting-started/start-using-dedicated-isp-proxies
- Decodo gateway and sticky session: https://decodo.com/blog/node-fetch-proxy
- IPRoyal REST v1: https://docs.iproyal.com/proxies/residential/api
- IPRoyal entry-nodes / proxy list: https://docs.iproyal.com/proxies/residential/api/access

Provider documentation may change. Check account-specific examples in the vendor dashboards before import or deployment. No live supplier accounts were queried during development.
