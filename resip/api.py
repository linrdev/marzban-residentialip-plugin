import asyncio
import hashlib
import hmac
import json
import logging
import re
import sqlite3
import time
from pathlib import Path
from contextlib import asynccontextmanager
from typing import Literal
from fastapi import FastAPI, Header, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field
from resip.db import init
from resip.marzban import Marzban
from resip.providers import gateway_profile
from resip.vendor_api import iproyal_entry_nodes
from resip.service import Service, Conflict, NotReady
from resip.settings import Settings

log = logging.getLogger(__name__)


class ExitIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str = Field(pattern=r"^[A-Za-z0-9_-]{1,48}$")
    provider: str = Field(max_length=64)
    host: str = Field(min_length=1, max_length=253)
    port: int = Field(ge=1, le=65535)
    username_env: str
    password_env: str
    expected_ip: str
    country: str = Field(pattern=r"^[A-Z]{2}$")
    supply_class: Literal["dedicated_residential", "dedicated_isp"]
    exclusive: Literal[True]  # Operator must explicitly attest exclusive upstream rights.


class Event(BaseModel):
    model_config = ConfigDict(extra="forbid")
    event_id: str = Field(pattern=r"^[a-zA-Z0-9:_-]{1,100}$")
    action: Literal["grant", "revoke"]
    username: str
    paid_until: int | None = None


async def loop_reconcile(service, poll_seconds):
    while True:
        await asyncio.sleep(poll_seconds)
        try:
            # Do not block the event loop with httpx sync and Marzban restarts.
            result = await asyncio.to_thread(service.reconcile, True)
            if result["applied"]:
                log.warning("Marzban core config changed and restarted")
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            log.error("Reconciliation failed: %s", type(exc).__name__)


def make_app(settings: Settings | None = None, marzban=None, probe=None):
    s = settings or Settings.from_env()
    s.validate()
    init(s.db_path)
    m = marzban or Marzban(s)
    service = Service(s, m, probe) if probe else Service(s, m)

    @asynccontextmanager
    async def lifespan(app):
        task = asyncio.create_task(loop_reconcile(service, s.poll_seconds)) if s.auto_apply else None
        yield
        if task:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
        if marzban is None:
            m.close()

    app = FastAPI(title="Residential Egress for Marzban", version="0.2.0", lifespan=lifespan,
                  description="Internal admin/API only. Do not publicly expose this service.")
    app.state.service = service

    def auth(token):
        if not token or not hmac.compare_digest(token, s.api_key):
            raise HTTPException(401, "Invalid admin API key")

    def fail(exc):
        if isinstance(exc, Conflict):
            raise HTTPException(409, str(exc)) from exc
        if isinstance(exc, (NotReady, ValueError)):
            raise HTTPException(422, str(exc)) from exc
        if isinstance(exc, sqlite3.IntegrityError):
            raise HTTPException(409, "Conflicting or duplicate resource") from exc
        # Deliberately hide internal provider secrets, URLs and upstream HTTP details.
        log.error("Upstream operation failed: %s", type(exc).__name__)
        raise HTTPException(502, "Upstream provider or Marzban operation failed") from exc

    @app.get("/healthz")
    def healthz():
        return {"healthy": True}  # Liveness only, not egress readiness.

    @app.get("/readyz")
    def readyz():
        # Local readiness only; upstream readiness remains authenticated /v1/status.
        try:
            with sqlite3.connect(s.db_path, timeout=3) as conn:
                conn.execute("SELECT 1 FROM events LIMIT 1").fetchall()
            if not Path(s.db_path).is_file():
                raise RuntimeError("State database missing")
        except Exception:
            raise HTTPException(503, "State database unavailable")
        return {"ready": True}

    @app.get("/v1/exits")
    def exits(x_admin_key: str | None = Header(default=None)):
        auth(x_admin_key)
        return service.list_exits()

    @app.post("/v1/exits", status_code=201)
    def add_exit(payload: ExitIn, x_admin_key: str | None = Header(default=None)):
        auth(x_admin_key)
        try:
            return service.add_exit(payload)
        except Exception as exc:
            fail(exc)

    @app.post("/v1/exits/{exit_id}/verify")
    def verify(exit_id: str, x_admin_key: str | None = Header(default=None)):
        auth(x_admin_key)
        try:
            return service.verify(exit_id)
        except Exception as exc:
            fail(exc)

    @app.get("/v1/gateways/{provider}")
    def provider_example(provider: str, username: str, session: str | None = None,
                         x_admin_key: str | None = Header(default=None)):
        auth(x_admin_key)
        if session is not None and not re.fullmatch(r"[A-Za-z0-9]{1,32}", session):
            raise HTTPException(422, "Invalid session identifier")
        try:
            p = gateway_profile(provider, username, session)
            return {"host": p.host, "port": p.port, "username": p.username,
                    "mode": p.mode, "exclusive": p.exclusive,
                    "note": "Gateway examples do not purchase, reserve or prove IP exclusivity"}
        except ValueError as exc:
            raise HTTPException(404, str(exc)) from exc

    @app.get("/v1/provider/iproyal/entry-nodes")
    def iproyal_catalog(x_admin_key: str | None = Header(default=None)):
        auth(x_admin_key)
        try:
            return iproyal_entry_nodes()
        except Exception as exc:
            fail(exc)

    @app.get("/v1/assignments")
    def assignments(x_admin_key: str | None = Header(default=None)):
        auth(x_admin_key)
        return service.list_assignments()

    @app.post("/v1/billing/events")
    async def billing_event(request: Request, x_resip_timestamp: str | None = Header(default=None),
                            x_resip_signature: str | None = Header(default=None)):
        body = await request.body()
        if len(body) > 8192:
            raise HTTPException(413, "Event too large")
        try:
            ts = int(x_resip_timestamp or "")
        except ValueError:
            raise HTTPException(401, "Missing valid signed timestamp")
        if abs(time.time() - ts) > 300:
            raise HTTPException(401, "Expired timestamp")
        payload = str(ts).encode() + b"." + body
        expected = hmac.new(s.billing_hmac_key.encode(), payload, hashlib.sha256).hexdigest()
        if not x_resip_signature or not hmac.compare_digest(expected, x_resip_signature):
            raise HTTPException(401, "Invalid billing signature")
        try:
            event = Event.model_validate_json(body)
            result = await asyncio.to_thread(service.receive, event.model_dump(), body)
            if s.auto_apply and result["status"] != "duplicate":
                apply_result = await asyncio.to_thread(service.reconcile, True)
                result["deployment"] = apply_result
            return result
        except Exception as exc:
            fail(exc)

    @app.get("/v1/status")
    def status(x_admin_key: str | None = Header(default=None)):
        auth(x_admin_key)
        try:
            plan = service.reconcile(False)
            rows = service.list_assignments()
            plan["pending"] = sum(a["state"] == "pending" for a in rows)
            plan["revoking"] = sum(a["state"] == "revoking" for a in rows)
            plan["healthy"] = (not plan["change_required"] and
                               not plan["blocked_premium"] and not plan["pending"] and
                               not plan["revoking"])
            return plan
        except Exception as exc:
            fail(exc)

    @app.post("/v1/reconcile")
    def reconcile(dry_run: bool = True, x_admin_key: str | None = Header(default=None)):
        auth(x_admin_key)
        try:
            return service.reconcile(not dry_run)
        except Exception as exc:
            fail(exc)

    return app


# Start with: uvicorn resip.api:make_app --factory
