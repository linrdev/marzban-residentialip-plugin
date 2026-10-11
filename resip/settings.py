import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Settings:
    db_path: str
    marzban_url: str
    marzban_user: str
    marzban_password: str
    marzban_db_url: str
    api_key: str
    billing_hmac_key: str
    auto_apply: bool
    probe_url: str
    probe_max_age: int
    poll_seconds: int
    allow_unsafe_http: bool

    @classmethod
    def from_env(cls):
        return cls(
            db_path=os.getenv("RESIP_DB", "/data/resip.db"),
            marzban_url=os.getenv("MARZBAN_URL", "https://marzban.internal").rstrip("/"),
            marzban_user=os.getenv("MARZBAN_SUDO_USER", ""),
            marzban_password=os.getenv("MARZBAN_SUDO_PASSWORD", ""),
            marzban_db_url=os.getenv("MARZBAN_DB_URL", "sqlite:///file:/marzban-ro/db.sqlite3?mode=ro&uri=true"),
            api_key=os.getenv("RESIP_API_KEY", ""),
            billing_hmac_key=os.getenv("RESIP_BILLING_HMAC_KEY", ""),
            auto_apply=os.getenv("RESIP_AUTO_APPLY", "false").lower() == "true",
            probe_url=os.getenv("RESIP_PROBE_URL", "https://api.ipify.org?format=json"),
            probe_max_age=int(os.getenv("RESIP_PROBE_MAX_AGE", "3600")),
            poll_seconds=int(os.getenv("RESIP_POLL_SECONDS", "60")),
            allow_unsafe_http=os.getenv("RESIP_ALLOW_HTTP_MARZBAN", "false").lower() == "true",
        )

    def validate(self):
        if any(value.startswith("REPLACE_") for value in
               (self.api_key, self.billing_hmac_key, self.marzban_password)):
            raise RuntimeError("Replace example credentials before starting")
        if not self.api_key or len(self.api_key) < 24:
            raise RuntimeError("RESIP_API_KEY must be a long random secret")
        if not self.billing_hmac_key or len(self.billing_hmac_key) < 24:
            raise RuntimeError("RESIP_BILLING_HMAC_KEY must be a long random secret")
        if self.api_key == self.billing_hmac_key:
            raise RuntimeError("Admin and billing keys must be different")
        if not self.marzban_user or not self.marzban_password:
            raise RuntimeError("Marzban credentials not configured")
        if not self.marzban_db_url:
            raise RuntimeError("Marzban SQL identity read source not configured")
        if not self.marzban_url.startswith("https://") and not (self.allow_unsafe_http and self.marzban_url.startswith("http://")):
            raise RuntimeError("MARZBAN_URL must be HTTPS unless explicit private-network override")
        if not self.probe_url.startswith("https://"):
            raise RuntimeError("Probe URL must use HTTPS")
        if self.poll_seconds < 15 or self.probe_max_age < 60:
            raise RuntimeError("Invalid probe/poll settings")
