"""Idempotent Marzban Xray overlay, scoped to RIP_ tags and user matches."""
from copy import deepcopy
from resip.providers import outbound

PREFIX = "RIP_"


def owned_tag(tag):
    return isinstance(tag, str) and tag.startswith(PREFIX)


def overlay(config: dict, entitlements: list[dict], exits: dict[str, dict], environ: dict) -> dict:
    c = deepcopy(config)
    if not isinstance(c.get("outbounds"), list) or not c["outbounds"]:
        raise ValueError("Marzban config missing outbounds")
    if not isinstance(c.get("routing"), dict):
        c["routing"] = {"rules": []}
    if not isinstance(c["routing"].get("rules", []), list):
        raise ValueError("Marzban config routing rules must be a list")
    original_out = [o for o in c["outbounds"] if not owned_tag(o.get("tag"))]
    original_rules = [r for r in c["routing"].get("rules", []) if not owned_tag(r.get("outboundTag"))]
    used_ids = set()
    users = []
    routes = []
    for item in sorted(entitlements, key=lambda row: row["username"]):
        row = exits[item["exit_id"]]
        if row["id"] in used_ids:
            raise ValueError("Same exit allocated to multiple subscribers")
        used_ids.add(row["id"])
        user = f"{item['marzban_id']}.{item['username']}"
        users.append(user)
        if item.get("blocked", False):
            routes.append({"type": "field", "user": [user], "outboundTag": "RIP_DENY"})
        else:
            routes.append({"type": "field", "user": [user], "outboundTag": f"RIP_EXIT_{row['id']}"})
            original_out.append(outbound(row, environ))
    if users:
        # Only premium users are affected. UDP unsupported in this SOCKS5-only MVP.
        guards = [
            {"type": "field", "user": users, "network": "udp", "outboundTag": "RIP_DENY"},
            {"type": "field", "user": users, "ip": ["geoip:private"], "outboundTag": "RIP_DENY"},
            {"type": "field", "user": users, "domain": ["full:localhost", "domain:localhost", "domain:local"], "outboundTag": "RIP_DENY"},
        ]
        original_out.append({"protocol": "blackhole", "tag": "RIP_DENY", "settings": {}})
        # Guard + user-specific premium match before preexisting generic direct rules.
        c["routing"]["rules"] = guards + routes + original_rules
    else:
        c["routing"]["rules"] = original_rules
    c["outbounds"] = original_out
    return c
