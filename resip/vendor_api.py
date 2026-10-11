"""Optional vendor control-plane integration.

IPRoyal's current v1 API exposes inventory/entry-node discovery; it does not
convert a sticky residential list into exclusive ownership of a public IP.
"""
import os
import httpx

IPROYAL_API = "https://resi-api.iproyal.com/v1"


def iproyal_entry_nodes(transport=None) -> dict:
    token = os.environ.get("IPROYAL_API_TOKEN")
    if not token:
        raise ValueError("IPROYAL_API_TOKEN is not configured")
    with httpx.Client(base_url=IPROYAL_API, transport=transport, timeout=12,
                      follow_redirects=False, trust_env=False) as client:
        r = client.get("/access/entry-nodes",
                       headers={"Authorization": "Bearer " + token})
        r.raise_for_status()
        data = r.json()
        # Read-only discovery. No provider-issued usernames, passwords or API tokens returned.
        return {"provider": "iproyal", "entry_nodes": data,
                "exclusive": False,
                "note": "API discovery is not an exclusive private IP allocation"}
