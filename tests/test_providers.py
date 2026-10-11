import httpx
import pytest
from resip.providers import gateway_profile
from resip.vendor_api import iproyal_entry_nodes


def test_gateway_profiles_are_nonexclusive_except_dedicated():
 assert gateway_profile('brightdata','brd-customer-a-zone-b','123').username.endswith('-session-123')
 assert gateway_profile('brightdata','brd-customer-a-zone-b','123').exclusive is False
 assert gateway_profile('oxylabs_residential','customer-alice','123').username.endswith('-sessid-123')
 assert gateway_profile('decodo_residential','user-bob','123').username.endswith('-session-123-sessionduration-30')
 assert gateway_profile('oxylabs_dedicated_isp','user-alice').port == 8001
 assert gateway_profile('oxylabs_dedicated_isp','user-alice').exclusive is True


def test_iproyal_read_only_api(monkeypatch):
 monkeypatch.setenv('IPROYAL_API_TOKEN','secret')
 def handle(request):
  assert request.url.path == '/v1/access/entry-nodes'
  assert request.headers['Authorization'] == 'Bearer secret'
  return httpx.Response(200,json={'data':['geo.iproyal.com']})
 result=iproyal_entry_nodes(transport=httpx.MockTransport(handle))
 assert result['entry_nodes']['data'] == ['geo.iproyal.com']
 assert not result['exclusive']
