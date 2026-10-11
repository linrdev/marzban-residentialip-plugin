import json
import threading
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
import time

import pytest
from fastapi.testclient import TestClient
from resip.api import make_app
from resip.db import transaction
from tests.test_service import grant, revoke


def test_released_exit_can_be_assigned_to_another_user(env, example_exit):
    _, _, service = env
    service.add_exit(example_exit)
    service.verify('eu1')
    grant(service, 'grant-alice')
    service.reconcile(True)
    revoke(service, 'revoke-alice')
    service.reconcile(True)
    assert grant(service, 'grant-bob', 'bob')['status'] == 'allocated_pending'
    service.reconcile(True)
    assert service.list_exits()[0]['allocated_to'] == 'bob'
    assert not any(rule.get('user') == ['42.alice'] for rule in service.m.config()['routing']['rules'])


def test_revocation_waits_for_inflight_apply(env, example_exit):
    _, marzban, service = env
    service.add_exit(example_exit)
    service.verify('eu1')
    grant(service, 'grant-alice')
    entered, release, attempted = threading.Event(), threading.Event(), threading.Event()
    original = marzban.put_config

    def slow_put(config):
        entered.set()
        assert release.wait(3)
        return original(config)

    def concurrent_revoke():
        attempted.set()
        return revoke(service, 'revoke-alice')

    marzban.put_config = slow_put
    with ThreadPoolExecutor(max_workers=2) as pool:
        applying = pool.submit(service.reconcile, True)
        assert entered.wait(3)
        revoking = pool.submit(concurrent_revoke)
        try:
            assert attempted.wait(3)
            assert service.list_assignments()[0]['state'] == 'pending'
            assert not revoking.done()
        finally:
            release.set()
        applying.result(timeout=3)
        revoking.result(timeout=3)
    assert service.list_assignments()[0]['state'] == 'revoking'
    assert service.list_exits()[0]['allocated_to'] == 'alice'
    service.reconcile(True)
    assert service.list_exits()[0]['allocated_to'] is None
    assert not any(r.get('user') == ['42.alice'] for r in marzban.config()['routing']['rules'])


def test_stale_probe_is_unhealthy_in_dry_run(env, example_exit):
    settings, _, service = env
    service.add_exit(example_exit)
    service.verify('eu1')
    grant(service, 'grant-alice')
    service.reconcile(True)
    with transaction(settings.db_path) as conn:
        conn.execute('UPDATE exits SET last_verified=?', (int(time.time()) - settings.probe_max_age - 1,))
    assert service.reconcile(False)['blocked_premium'] == 1
    assert service.reconcile(True)['blocked_premium'] == 0


def test_readiness_reports_missing_local_schema(env):
    settings, marzban, _ = env
    with TestClient(make_app(settings, marzban)) as client:
        assert client.get('/healthz').status_code == 200
        assert client.get('/readyz').status_code == 200
        with transaction(settings.db_path) as conn:
            conn.execute('DROP TABLE events')
        assert client.get('/readyz').status_code == 503
        assert client.get('/healthz').status_code == 200


@pytest.mark.parametrize('invalid', [None, 0, 'not-a-timestamp'])
def test_grant_requires_future_timestamp(env, invalid):
    _, _, service = env
    event = dict(event_id='bad-grant', username='alice', action='grant', paid_until=invalid)
    with pytest.raises(ValueError, match='future paid_until'):
        service.receive(event, json.dumps(event).encode())


def test_secret_validation_rejects_placeholder_or_shared_keys(env):
    settings, _, _ = env
    with pytest.raises(RuntimeError, match='different'):
        replace(settings, billing_hmac_key=settings.api_key).validate()
    with pytest.raises(RuntimeError, match='example credentials'):
        replace(settings, api_key='REPLACE_WITH_RANDOM_32PLUS_BYTES').validate()
