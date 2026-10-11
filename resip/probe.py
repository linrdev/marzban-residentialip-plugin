import ipaddress
import os
import httpx


def probe_exit(row: dict, probe_url: str) -> str:
    """Check exit through SOCKS5 (socks5h DNS at upstream), never direct fallback."""
    username = os.environ.get(row["username_env"])
    password = os.environ.get(row["password_env"])
    if not username or not password:
        raise ValueError("Missing upstream proxy credential variables")
    from urllib.parse import quote
    proxy = (f"socks5://{quote(username, safe='')}:{quote(password, safe='')}@"
             f"{row['host']}:{row['port']}")
    with httpx.Client(proxy=proxy, timeout=httpx.Timeout(12), trust_env=False,
                      follow_redirects=False) as client:
        result = client.get(probe_url)
        result.raise_for_status()
        observed = str(ipaddress.ip_address(result.json()["ip"]))
    if observed != str(ipaddress.ip_address(row["expected_ip"])):
        raise ValueError(f"Observed IP mismatch: expected {row['expected_ip']}, got {observed}")
    return observed
