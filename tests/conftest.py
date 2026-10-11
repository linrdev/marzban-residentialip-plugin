import copy
import pytest
from resip.settings import Settings
from resip.db import init
from resip.service import Service

BASE_CONFIG = {
 "routing": {"rules": [
   {"type": "field", "ip": ["geoip:private"], "outboundTag": "BLOCK"},
   {"type": "field", "domain": ["full:example.org"], "outboundTag": "DIRECT"}
 ]},
 "inbounds": [{"tag": "VLESS TCP", "protocol": "vless", "port": 443,
              "settings": {"clients": []}}],
 "outbounds": [{"tag": "DIRECT", "protocol": "freedom"},
               {"tag": "BLOCK", "protocol": "blackhole"}]
}

class FakeMarzban:
 def __init__(self):
  self.users = {"alice": {"id": 42, "username": "alice", "status": "active"},
                "bob": {"id": 43, "username": "bob", "status": "active"}}
  self.c = copy.deepcopy(BASE_CONFIG)
  self.writes = 0
  self.fail_put = False
 def user(self, username):
  return self.users[username]
 def config(self):
  return copy.deepcopy(self.c)
 def put_config(self, config):
  if self.fail_put:
   raise RuntimeError("Marzban disconnected")
  self.c = copy.deepcopy(config)
  self.writes += 1

@pytest.fixture
def env(monkeypatch, tmp_path):
 monkeypatch.setenv("PROXY_ONE_USERNAME", "demo_user")
 monkeypatch.setenv("PROXY_ONE_PASSWORD", "demo_password")
 s = Settings(db_path=str(tmp_path/"resip.db"),marzban_url="https://marzban.test",
              marzban_user="sudo",marzban_password="secret",marzban_db_url="sqlite:///file:/unused/marzban.db?mode=ro&uri=true",api_key="A"*32,
              billing_hmac_key="B"*32,auto_apply=False,probe_url="https://api.ipify.org?format=json",
              probe_max_age=3600,poll_seconds=60,allow_unsafe_http=False)
 init(s.db_path)
 m = FakeMarzban()
 svc = Service(s, m, lambda row,url: "8.8.4.4")
 return s,m,svc

@pytest.fixture
def example_exit():
 return dict(id="eu1", provider="oxylabs_dedicated_isp", host="disp.oxylabs.io",
             port=8001,username_env="PROXY_ONE_USERNAME",password_env="PROXY_ONE_PASSWORD",
             expected_ip="8.8.4.4",country="DE",supply_class="dedicated_isp",exclusive=True)
