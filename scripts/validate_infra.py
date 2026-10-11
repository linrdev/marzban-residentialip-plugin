#!/usr/bin/env python3
"""Render-only infrastructure checks; never start Docker or contact a cluster."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile

import yaml

ROOT = Path(__file__).resolve().parents[1]
HELM = os.environ.get('HELM_BINARY') or shutil.which('helm')
COMPOSE = os.environ.get('COMPOSE_BINARY') or shutil.which('docker')
if not HELM or not COMPOSE:
    raise SystemExit('Install Helm and Compose v2, or set HELM_BINARY and COMPOSE_BINARY.')
COMPOSE_CMD = [COMPOSE] if os.environ.get('COMPOSE_BINARY') else [COMPOSE, 'compose']


def run(args, *, cwd=ROOT, succeeds=True):
    result = subprocess.run(args, cwd=cwd, capture_output=True, text=True, timeout=60)
    if succeeds and result.returncode:
        raise RuntimeError(result.stdout + result.stderr)
    if not succeeds and result.returncode == 0:
        raise AssertionError(f'Unsafe values unexpectedly accepted: {args[-1]}')
    return result.stdout


def render(chart, *extra):
    text = run([HELM, 'template', 'resip', chart, '--namespace', 'resip', *extra])
    docs = [d for d in yaml.safe_load_all(text) if d]
    deployment = next(d for d in docs if d['kind'] == 'Deployment')
    assert deployment['spec']['replicas'] == 1
    assert deployment['spec']['strategy']['type'] == 'Recreate'
    pod = deployment['spec']['template']['spec']
    assert pod['automountServiceAccountToken'] is False
    addon = next(c for c in pod['containers'] if c['name'] == 'residential-addon')
    assert addon['securityContext']['readOnlyRootFilesystem']
    assert addon['securityContext']['capabilities']['drop'] == ['ALL']
    assert addon['readinessProbe']['httpGet']['path'] == '/readyz'
    assert all(d['spec']['type'] == 'ClusterIP' for d in docs if d['kind'] == 'Service')
    assert all(d['metadata']['annotations']['helm.sh/resource-policy'] == 'keep'
               for d in docs if d['kind'] == 'PersistentVolumeClaim')
    assert all(d['kind'] != 'Secret' for d in docs)
    for d in docs:
        if d['kind'] == 'Ingress':
            assert d['spec']['tls']
            for rule in d['spec']['rules']:
                assert all(p['path'] == '/vless-ws' and p['backend']['service']['port']['name'] == 'vless'
                           for p in rule['http']['paths'])
    return docs, pod


def validate_helm():
    base, stack = 'charts/residential-egress', 'charts/marzban-residential-stack'
    run([HELM, 'dependency', 'build', stack])
    for chart in (base, stack):
        run([HELM, 'lint', '--strict', chart])
    render(base)
    render(base, '-f', 'deploy/helm-existing.example.yaml')
    render(base, '--set', 'identity.existingClaim=live-marzban', '--set',
           'config.marzbanDbUrl=sqlite:///file:/marzban-ro/db.sqlite3?mode=ro&uri=true')
    docs, _ = render(base, '--set', 'persistence.existingClaim=retained-data')
    assert not any(d['kind'] == 'PersistentVolumeClaim' for d in docs)
    render(base, '--set', 'persistence.accessMode=ReadWriteOncePod')
    render(base, '--set', 'networkPolicy.egress={}')
    _, pod = render(stack)
    assert {c['name'] for c in pod['containers']} == {'residential-addon', 'marzban'}
    marzban = next(c for c in pod['containers'] if c['name'] == 'marzban')
    assert next(m for m in marzban['volumeMounts'] if m['name'] == 'marzban-data')['mountPath'] == '/var/lib/marzban'
    render(stack, '-f', 'deploy/helm-stack.example.yaml')
    for override in ('replicaCount=2', 'existingSecret=', 'config.marzbanUrl=http://public.test',
                     'config.pollSeconds=1', 'config.marzbanDbUrl=mysql+pymysql://user:secret@db/marzban',
                     'marzban.ingress.enabled=true'):
        run([HELM, 'template', 'resip', base, '--set', override], succeeds=False)
    assert (ROOT / 'deploy/xray_config.json').read_bytes() == (ROOT / base / 'files/xray_config.json').read_bytes()
    print('PASS: both Helm charts; existing/stack/TLS/PVC variants; unsafe-value rejection')


def validate_compose():
    # Only synthetic credentials; actual .env is never read or printed.
    with tempfile.TemporaryDirectory(prefix='resip-compose-') as temp:
        directory = Path(temp)
        for relative in ('compose.yaml', 'compose.mysql.yaml', 'deploy/compose.stack.yaml',
                         'deploy/Caddyfile', 'deploy/xray_config.json'):
            target = directory / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(ROOT / relative, target)
        fake = ('RESIP_API_KEY=' + 'A' * 32 + '\nRESIP_BILLING_HMAC_KEY=' + 'B' * 32 + '\n'
                'MARZBAN_SUDO_USER=staging\nMARZBAN_SUDO_PASSWORD=fake-only\n'
                'MARZBAN_URL=https://marzban.test\nMARZBAN_DB_URL=sqlite:///file:/marzban-ro/db.sqlite3?mode=ro&uri=true\n'
                'PROXY_ONE_USERNAME=fake\nPROXY_ONE_PASSWORD=fake\n'
                'PANEL_DOMAIN=panel.test\nVPN_DOMAIN=vpn.test\nACME_EMAIL=admin@example.test\n')
        (directory / '.env').write_text(fake)

        def config(*flags):
            return json.loads(run([*COMPOSE_CMD, '--env-file', '.env', *flags,
                                   'config', '--format', 'json'], cwd=directory))

        default = config('-f', 'compose.yaml')
        addon = default['services']['residential-addon']
        assert all(p['host_ip'] == '127.0.0.1' for p in addon['ports'])
        assert addon['read_only'] and addon['cap_drop'] == ['ALL']
        mysql = config('-f', 'compose.yaml', '-f', 'compose.mysql.yaml')
        assert [v['target'] for v in mysql['services']['residential-addon']['volumes']] == ['/data']
        stack = config('-f', 'deploy/compose.stack.yaml')
        assert 'caddy' not in stack['services']
        assert stack['services']['residential-addon']['environment']['MARZBAN_URL'] == 'http://marzban:8000'
        tls = config('-f', 'deploy/compose.stack.yaml', '--profile', 'tls')
        assert 'caddy' in tls['services']
        assert all(p['host_ip'] == '127.0.0.1' for service in ('marzban', 'residential-addon')
                   for p in tls['services'][service]['ports'])
        assert not any(p['target'] == 8787 for p in tls['services']['caddy']['ports'])
    print('PASS: Compose existing SQLite, MySQL override, full stack, and TLS profile')


validate_helm()
validate_compose()
