"""Provider-agnostic SOCKS5 data-plane; convenience gateway adapters are non-exclusive.

A gateway session is not proof of a private IP. Only explicitly imported fixed
endpoints can be allocated for the 'private residential IP' product.
"""
from dataclasses import dataclass


@dataclass(frozen=True)
class Gateway:
    host: str
    port: int
    username: str
    mode: str  # rotating | sticky | dedicated
    exclusive: bool


def gateway_profile(provider: str, username: str, session: str | None = None) -> Gateway:
    """Produce public documented gateway examples (no network calls or credentials).

    For named commercial accounts, input username must be the *full* provider-issued
    username; it is never derived from a guessed customer or zone identifier.
    """
    if provider == "brightdata":
        return Gateway("brd.superproxy.io", 22228, username + (f"-session-{session}" if session else ""),
                       "sticky" if session else "rotating", False)
    if provider == "oxylabs_residential":
        return Gateway("pr.oxylabs.io", 7777, username + (f"-sessid-{session}" if session else ""),
                       "sticky" if session else "rotating", False)
    if provider == "decodo_residential":
        # Decodo standard form: user-<name>-session-<id>-sessionduration-30
        return Gateway("gate.decodo.com", 7000,
                       username + (f"-session-{session}-sessionduration-30" if session else ""),
                       "sticky" if session else "rotating", False)
    if provider == "oxylabs_dedicated_isp":
        return Gateway("disp.oxylabs.io", 8001, username, "dedicated", True)
    raise ValueError("Unknown provider; use a manually imported gateway for others")


def outbound(exit_row: dict, environ: dict) -> dict:
    """Compile a fixed SOCKS5 exit, never interpolate untrusted URL strings."""
    username = environ.get(exit_row["username_env"])
    password = environ.get(exit_row["password_env"])
    if not username or not password:
        raise ValueError(f"Missing proxy credentials for {exit_row['id']}")
    return {
        "protocol": "socks", "tag": f"RIP_EXIT_{exit_row['id']}",
        "settings": {"address": exit_row["host"], "port": exit_row["port"],
                     "user": username, "pass": password}
    }
