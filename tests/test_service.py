import json
import time
from copy import deepcopy
import pytest
from resip.routing import overlay
from resip.service import Conflict, NotReady
from tests.conftest import BASE_CONFIG


def grant(svc, eventid, name="alice"):
 event = {"event_id":eventid,"action":"grant","username":name,"paid_until":int(time.time())+7200}
 return svc.receive(event,json.dumps(event).encode())


def revoke(svc, eventid, name="alice"):
 event = {"event_id":eventid,"action":"revoke","username":name}
 return svc.receive(event,json.dumps(event).encode())


def test_private_allocation_apply_and_revoke(env, example_exit):
 s,m,svc = env
 svc.add_exit(example_exit)
 with pytest.raises(NotReady): grant(svc,"evt1")
 svc.verify("eu1")
 assert grant(svc,"evt1")["status"] == "allocated_pending"
 assert svc.list_assignments()[0]["state"] == "pending"
 d = svc.reconcile(False)
 assert d["change_required"]
 assert m.writes == 0
 result = svc.reconcile(True)
 assert result["core_restart"]
 assert svc.list_assignments()[0]["state"] == "active"
 assert m.writes == 1
 assert svc.reconcile(True)["core_restart"] is False
 assert m.writes == 1
 conf = m.config()
 assert conf["outbounds"][0]["tag"] == "DIRECT"  # standard user unaffected
 assert conf["routing"]["rules"][0]["network"] == "udp"
 assert conf["routing"]["rules"][0]["user"] == ["42.alice"]
 assert conf["routing"]["rules"][3]["outboundTag"] == "RIP_EXIT_eu1"
 assert conf["routing"]["rules"][3]["user"] == ["42.alice"]
 assert conf["routing"]["rules"][-1] == BASE_CONFIG["routing"]["rules"][-1]
 assert grant(svc,"evt1")["status"] == "duplicate"
 assert revoke(svc,"evt2")["status"] == "revocation_pending"
 # Do not release exit until Marzban core has acknowledged routing removal.
 assert svc.list_exits()[0]["allocated_to"] == "alice"
 svc.reconcile(True)
 assert svc.list_exits()[0]["allocated_to"] is None
 assert svc.list_assignments()[0]["state"] == "revoked"
 assert all(not r["outboundTag"].startswith("RIP_") for r in m.config()["routing"]["rules"])


def test_double_allocation_and_idempotency(env, example_exit):
 _,m,svc=env
 svc.add_exit(example_exit)
 svc.verify("eu1")
 grant(svc,"evtA")
 with pytest.raises(NotReady): grant(svc,"evtB","bob")
 with pytest.raises(Conflict):
  evt={"event_id":"evtA","action":"revoke","username":"alice"}
  svc.receive(evt,json.dumps(evt).encode())
 assert svc.list_exits()[0]["allocated_to"] == "alice"


def test_failed_apply_keeps_lease(env, example_exit):
 _,m,svc=env
 svc.add_exit(example_exit)
 svc.verify("eu1")
 grant(svc,"evtA")
 m.fail_put = True
 with pytest.raises(RuntimeError): svc.reconcile(True)
 assert svc.list_assignments()[0]["state"] == "pending"
 assert svc.list_exits()[0]["allocated_to"] == "alice"
 m.fail_put = False
 assert svc.reconcile(True)["core_restart"]


def test_failed_proxy_probe_blocks_existing_premium_user(env, example_exit):
 _,m,svc=env
 svc.add_exit(example_exit)
 svc.verify("eu1")
 grant(svc,"evtA")
 svc.reconcile(True)
 svc.probe=lambda row,url: "1.1.1.1"
 with pytest.raises(NotReady): svc.verify("eu1") # error before config update
 result=svc.reconcile(True)
 assert result["blocked_premium"] == 1
 assert svc.list_assignments()[0]["state"] == "active"
 rules=m.config()["routing"]["rules"]
 assert any(r.get("user")==["42.alice"] and r["outboundTag"]=="RIP_DENY" for r in rules)
 assert all(o["tag"] != "RIP_EXIT_eu1" for o in m.config()["outbounds"])


def test_no_fallback_for_missing_credential(env, example_exit, monkeypatch):
 _,_,svc=env
 svc.add_exit(example_exit)
 svc.verify("eu1")
 grant(svc,"evtA")
 monkeypatch.delenv("PROXY_ONE_PASSWORD")
 result=svc.reconcile(True)
 assert result['blocked_premium'] == 1
 assert any(r.get('user')==['42.alice'] and r['outboundTag']=='RIP_DENY'
            for r in svc.m.config()['routing']['rules'])


def test_provider_rotating_port_refused(env, example_exit):
 _,_,svc=env
 example_exit["port"]=8000
 with pytest.raises(ValueError):
  svc.add_exit(example_exit)


def test_rotating_provider_cannot_claim_private(env, example_exit):
 _,_,svc=env
 for name in ("brightdata", "oxylabs_residential", "decodo_residential", "iproyal_residential"):
  example_exit["provider"] = name
  with pytest.raises(ValueError):
   svc.add_exit(example_exit)


def test_route_overlay_idempotent_and_preserves_other_rules():
 base=deepcopy(BASE_CONFIG)
 envvars={"PUSER":"abc", "PPASS":"xyz"}
 exits={"eu1":dict(id="eu1",host="proxy.example.org",port=1080,username_env="PUSER",password_env="PPASS")}
 e=[dict(username="alice",marzban_id=42,exit_id="eu1")]
 a=overlay(base,e,exits,envvars)
 b=overlay(a,e,exits,envvars)
 assert a==b
 assert BASE_CONFIG["outbounds"] == base["outbounds"]
 assert overlay(a,[],exits,envvars) == BASE_CONFIG


def test_expired_plan_revoke_before_release(env, example_exit):
 _,m,svc=env
 svc.add_exit(example_exit)
 svc.verify("eu1")
 grant(svc,"evtA")
 svc.reconcile(True)
 from resip.db import transaction
 with transaction(svc.s.db_path) as c:
  c.execute("UPDATE entitlements SET paid_until=?",(int(time.time())-10,))
 assert svc.reconcile(True)["revocations_completed"] == 1
 assert svc.list_exits()[0]["allocated_to"] is None
