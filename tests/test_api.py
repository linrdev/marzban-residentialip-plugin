import hashlib
import hmac
import json
import time
from fastapi.testclient import TestClient
from resip.api import make_app


def test_signed_event_auth_and_replay(env, example_exit):
 s,m,svc = env
 api=make_app(s, marzban=m, probe=lambda row,url: "8.8.4.4")
 with TestClient(api) as client:
  assert client.get('/v1/exits').status_code == 401
  headers={"x-admin-key":s.api_key}
  assert client.post('/v1/exits', json=example_exit, headers=headers).status_code == 201
  assert client.post('/v1/exits/eu1/verify',headers=headers).status_code == 200
  evt={"event_id":"stripe-entitlement-1001","action":"grant","username":"alice","paid_until":int(time.time())+3600}
  body=json.dumps(evt,separators=(',',':')).encode()
  ts=str(int(time.time()))
  sig=hmac.new(s.billing_hmac_key.encode(),ts.encode()+b'.'+body,hashlib.sha256).hexdigest()
  signed={"x-resip-timestamp":ts,"x-resip-signature":sig,"content-type":"application/json"}
  assert client.post('/v1/billing/events',content=body,headers=signed).json()['status']=='allocated_pending'
  assert client.post('/v1/billing/events',content=body,headers=signed).json()['status']=='duplicate'
  assert client.post('/v1/billing/events',content=body,headers={"x-resip-timestamp":ts,"x-resip-signature":"bad"}).status_code==401
  assert client.post('/v1/billing/events',content=body,headers={"x-resip-timestamp":str(int(ts)-600),"x-resip-signature":sig}).status_code==401
  assert client.post('/v1/reconcile',headers=headers).json()['change_required']
  assert client.post('/v1/reconcile?dry_run=false',headers=headers).json()['applied']
  assert client.get('/v1/assignments',headers=headers).json()[0]['state']=='active'
  assert client.get('/v1/status',headers=headers).json()['healthy'] is True
