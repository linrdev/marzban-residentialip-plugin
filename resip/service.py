"""Transactional allocator and Marzban reconciler.

SQLite means a single service instance is supported. No payments are trusted until
an upstream billing service validates them and signs the internal event.
"""
import hashlib
import ipaddress
import json
import os
import re
import time
from threading import RLock
from resip.db import transaction, select_all
from resip.routing import overlay
from resip.probe import probe_exit

ID = re.compile(r"^[A-Za-z0-9_-]{1,48}$")
USERNAME = re.compile(r"^[a-z0-9_]{3,32}$")
ENV_NAME = re.compile(r"^[A-Z][A-Z0-9_]{2,80}$")


class Conflict(Exception):
    pass


class NotReady(Exception):
    pass


class Service:
    def __init__(self, settings, marzban, probe=probe_exit):
        self.s = settings
        self.m = marzban
        self.probe = probe
        self.apply_lock = RLock()

    def add_exit(self, value):
        v = value.model_dump() if hasattr(value, "model_dump") else value
        if not ID.fullmatch(v["id"]):
            raise ValueError("Invalid exit ID")
        for key in ("username_env", "password_env"):
            if not ENV_NAME.fullmatch(v[key]):
                raise ValueError("Credentials must reference environment variable names")
            if not os.environ.get(v[key]):
                raise ValueError(f"Credential environment variable {v[key]} not set")
        addr = ipaddress.ip_address(v["expected_ip"])
        if not addr.is_global:
            raise ValueError("Expected residential exit IP must be globally routable")
        if v["provider"] in {"brightdata", "oxylabs_residential", "decodo_residential", "iproyal_residential"}:
            raise ValueError("Shared/rotating residential gateways cannot be sold as exclusive fixed IPs")
        if v["provider"] == "oxylabs_dedicated_isp" and v["port"] < 8001:
            raise ValueError("Oxylabs dedicated ISP requires fixed per-IP ports from 8001")
        with transaction(self.s.db_path) as c:
            c.execute("""INSERT INTO exits(id,provider,host,port,username_env,password_env,
              expected_ip,country,supply_class,exclusive) VALUES (?,?,?,?,?,?,?,?,?,1)""",
              (v['id'],v['provider'],v['host'],v['port'],v['username_env'],v['password_env'],
               str(addr),v['country'],v['supply_class']))
        return {"id": v["id"], "state": "unverified"}

    def list_exits(self):
        with transaction(self.s.db_path) as c:
            return select_all(c,"""SELECT id,provider,host,port,expected_ip,country,supply_class,
               enabled,observed_ip,last_verified,allocated_to FROM exits ORDER BY id""")

    def verify(self, exit_id):
        with transaction(self.s.db_path) as c:
            row = c.execute("SELECT * FROM exits WHERE id=?", (exit_id,)).fetchone()
            if row is None:
                raise ValueError("Unknown exit")
            row = dict(row)
        try:
            observed = self.probe(row, self.s.probe_url)
        except Exception:
            with transaction(self.s.db_path) as c:
                c.execute("UPDATE exits SET last_verified=NULL, observed_ip=NULL WHERE id=?", (exit_id,))
            raise
        if observed != row["expected_ip"]:
            with transaction(self.s.db_path) as c:
                c.execute("UPDATE exits SET last_verified=NULL, observed_ip=NULL WHERE id=?", (exit_id,))
            raise NotReady("Residential exit IP mismatch")
        with transaction(self.s.db_path) as c:
            c.execute("UPDATE exits SET last_verified=?, observed_ip=? WHERE id=?",
                      (int(time.time()), observed, exit_id))
        return {"id": exit_id, "observed_ip": observed, "verified": True}

    def receive(self, event: dict, payload: bytes):
        # Keep lease mutations outside an in-flight config apply. Otherwise a
        # revoke may release an exit after the old snapshot just routed it.
        with self.apply_lock:
            return self._receive(event, payload)

    def _receive(self, event: dict, payload: bytes):
        name = event["username"]
        if not USERNAME.fullmatch(name):
            raise ValueError("Invalid Marzban username")
        action = event["action"]
        now = int(time.time())
        marzban_user = None
        if action == "grant":
            if not isinstance(event.get("paid_until"), int) or event["paid_until"] <= now:
                raise ValueError("Grant must have future paid_until epoch seconds")
            marzban_user = self.m.user(name)
            if marzban_user.get("status") != "active":
                raise Conflict("Only active Marzban users eligible for assignment")
        digest = hashlib.sha256(payload).hexdigest()
        with transaction(self.s.db_path) as c:
            seen = c.execute("SELECT payload_sha256 FROM events WHERE event_id=?", (event['event_id'],)).fetchone()
            if seen:
                if seen[0] != digest:
                    raise Conflict("Event ID collision with different payload")
                return {"event_id": event['event_id'], "status": "duplicate"}
            if action == "grant":
                current = c.execute("SELECT * FROM entitlements WHERE username=?", (name,)).fetchone()
                if current and current['state'] in ('pending','active'):
                    if current['marzban_id'] != marzban_user['id']:
                        raise Conflict("Marzban user identity changed; manual intervention required")
                    c.execute("UPDATE entitlements SET paid_until=?,updated=? WHERE username=?",
                              (event['paid_until'], now, name))
                    outcome = "renewed"
                else:
                    if current and current['state'] == 'revoking':
                        raise Conflict("Previous exit revocation still needs apply")
                    exit_row = c.execute("""SELECT * FROM exits WHERE enabled=1 AND allocated_to IS NULL
                       AND last_verified>=? AND observed_ip=expected_ip
                       ORDER BY id LIMIT 1""", (now-self.s.probe_max_age,)).fetchone()
                    if not exit_row:
                        raise NotReady("No verified exclusive residential exit available")
                    if current:
                        c.execute("DELETE FROM entitlements WHERE username=?", (name,))
                    # Revoked rows must not hold the UNIQUE exit_id forever.
                    # Persisted event IDs retain billing idempotency after reuse.
                    c.execute("DELETE FROM entitlements WHERE exit_id=? AND state='revoked'", (exit_row['id'],))
                    c.execute("""INSERT INTO entitlements(username,marzban_id,exit_id,paid_until,state,updated)
                         VALUES (?,?,?,?,?,?)""", (name,marzban_user['id'],exit_row['id'],event['paid_until'],'pending',now))
                    c.execute("UPDATE exits SET allocated_to=? WHERE id=?", (name,exit_row['id']))
                    outcome = "allocated_pending"
            else:
                current = c.execute("SELECT state FROM entitlements WHERE username=?", (name,)).fetchone()
                if current and current['state'] not in ('revoked','revoking'):
                    c.execute("UPDATE entitlements SET state='revoking', updated=? WHERE username=?", (now,name))
                outcome = "revocation_pending"
            c.execute("INSERT INTO events(event_id,payload_sha256,created) VALUES(?,?,?)",
                      (event['event_id'],digest,now))
        return {"event_id": event['event_id'], "status": outcome}

    def list_assignments(self):
        with transaction(self.s.db_path) as c:
            return select_all(c, "SELECT username,marzban_id,exit_id,paid_until,state,updated FROM entitlements ORDER BY username")

    def reconcile(self, write=False):
        with self.apply_lock:
            now = int(time.time())
            with transaction(self.s.db_path) as c:
                if write:
                    c.execute("UPDATE entitlements SET state='revoking' WHERE paid_until<=? AND state IN ('active','pending')", (now,))
                # Build from persisted leases, never accept arbitrary client routing identities.
                active = select_all(c,"""SELECT username,marzban_id,exit_id,paid_until,state
                    FROM entitlements WHERE state IN ('active','pending') AND paid_until>?""",(now,))
                ids = [a['exit_id'] for a in active]
                exits = {r['id']: r for r in select_all(c, "SELECT * FROM exits")}
            for a in active:
                e = exits[a['exit_id']]
                # On a failed/aged probe, route to RIP_DENY. Never route to DIRECT.
                if write and (not e['last_verified'] or now - e['last_verified'] > self.s.probe_max_age // 2):
                    try:
                        self.verify(e['id'])
                    except Exception as exc:
                        # Log error type only; upstream exception may contain credentials.
                        import logging
                        logging.getLogger(__name__).warning("Exit verification failed: %s (%s)", e['id'], type(exc).__name__)
                    with transaction(self.s.db_path) as conn:
                        new = conn.execute("SELECT * FROM exits WHERE id=?", (e['id'],)).fetchone()
                        exits[e['id']] = dict(new)
                    e = exits[e['id']]
                if (not e['enabled'] or not e['last_verified'] or
                    now - e['last_verified'] > self.s.probe_max_age or
                    e['observed_ip'] != e['expected_ip'] or
                    not os.environ.get(e['username_env']) or
                    not os.environ.get(e['password_env'])):
                    a['blocked'] = True
                try:
                    remote = self.m.user(a['username'])
                    if remote['id'] != a['marzban_id'] or remote.get('status') != 'active':
                        a['blocked'] = True
                except Exception:
                    a['blocked'] = True
            remote_config = self.m.config()
            desired = overlay(remote_config, active, exits, os.environ)
            changed = (desired != remote_config)
            if not write:
                return {"change_required": changed, "premium_users": len(active),
                        "managed_outbounds": len(ids)-sum(1 for a in active if a.get("blocked")), "blocked_premium": sum(1 for a in active if a.get("blocked")), "write_enabled": False}
            if changed:
                self.m.put_config(desired)  # Restarts core AND all connected nodes.
                actual = self.m.config()
                if actual != desired:
                    raise NotReady("Marzban config did not match after apply")
            with transaction(self.s.db_path) as c:
                for a in active:
                    if not a.get('blocked'):
                        c.execute("UPDATE entitlements SET state='active' WHERE username=? AND state='pending' AND paid_until>?",
                                  (a['username'],now))
                revoked = select_all(c, "SELECT username,exit_id FROM entitlements WHERE state='revoking'")
                for item in revoked:
                    c.execute("UPDATE exits SET allocated_to=NULL WHERE id=? AND allocated_to=?",
                              (item['exit_id'],item['username']))
                c.execute("UPDATE entitlements SET state='revoked' WHERE state='revoking'")
            return {"applied": changed, "premium_users": len(active),
                    "core_restart": changed, "blocked_premium": sum(1 for a in active if a.get("blocked")), "revocations_completed": len(revoked)}
