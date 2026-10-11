import argparse
import base64
import importlib.util
import io
import json
from pathlib import Path
import sqlite3
import tarfile

import pytest

SPEC = importlib.util.spec_from_file_location('resip_admin', Path(__file__).parents[1] / 'scripts/admin.py')
admin = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(admin)


@pytest.fixture
def configured(tmp_path):
    root = tmp_path / 'resip'
    root.mkdir()
    config = {'version': 1, 'preset': 'existing-sqlite', 'project': 'resip'}
    values = {'RESIP_API_KEY': 'A' * 32, 'RESIP_BILLING_HMAC_KEY': 'B' * 32,
              'MARZBAN_SUDO_USER': 'operator', 'MARZBAN_SUDO_PASSWORD': 'fake-password',
              'MARZBAN_URL': 'https://marzban.test', 'RESIP_AUTO_APPLY': 'true'}
    admin.private_write(root / '.env', admin.format_env(values))
    admin.private_write(root / '.resip-admin.json', json.dumps(config))
    owner = admin.Admin(root)
    return owner, config, values


def database_bytes(tmp_path):
    p = tmp_path / 'test-state.db'
    with sqlite3.connect(p) as connection:
        connection.execute('CREATE TABLE IF NOT EXISTS test (id INTEGER)')
    return p.read_bytes()


def complete_snapshot(tmp_path, config, values):
    dest = tmp_path / 'snapshot'
    dest.mkdir()
    admin.private_write(dest / 'files/.env', admin.format_env(values))
    admin.private_write(dest / 'files/.resip-admin.json', json.dumps(config))
    admin.private_write(dest / 'resip.db', database_bytes(tmp_path))
    admin.private_write(dest / 'core.json', json.dumps({'outbounds': [{'tag': 'DIRECT', 'protocol': 'freedom'}]}))
    manifest = {'version': 1, 'settings': config, 'git_sha': 'a' * 40,
                'images': {'residential-addon': 'sha256:' + 'b' * 64},
                'sha256': {str(p.relative_to(dest)): admin.digest_file(p) for p in dest.rglob('*') if p.is_file()}}
    admin.private_write(dest / 'manifest.json', json.dumps(manifest))
    return dest


def test_dotenv_keeps_metacharacters_literal():
    values = {'PASSWORD': "space # hash $HOME $(touch /tmp/never) `id` 'quote' \\ slash"}
    assert admin.parse_env(admin.format_env(values)) == values
    with pytest.raises(admin.AdminError):
        admin.parse_env('$(touch /tmp/never)')


def test_private_files_and_mutual_exclusion(configured):
    owner, _, _ = configured
    assert (owner.root / '.env').stat().st_mode & 0o777 == 0o600
    with owner.lock():
        with pytest.raises(admin.AdminError, match='Another'):
            with admin.Admin(owner.root).lock():
                pass


@pytest.mark.parametrize('preset', list(admin.PRESETS))
def test_preset_selects_correct_compose_files(configured, preset):
    owner, config, _ = configured
    config['preset'] = preset
    admin.private_write(owner.root / '.resip-admin.json', json.dumps(config))
    command = owner.compose()
    mode = admin.PRESETS[preset][1]
    assert any('compose.stack.yaml' in arg for arg in command) == mode.startswith('stack')
    assert any('compose.mysql.yaml' in arg for arg in command) == (mode == 'mysql')
    assert ('--profile' in command) == (mode == 'stack-tls')


def test_noninteractive_wizard_generates_distinct_keys_and_no_secret_output(tmp_path, capsys):
    root = tmp_path / 'new-stack'
    root.mkdir()
    (root / '.env.example').write_text((Path(__file__).parents[1] / '.env.example').read_text())
    owner = admin.Admin(root)
    args = argparse.Namespace(preset='stack-lab', dry_run=False, non_interactive=True, project=None, yes=True)
    admin.wizard(owner, args)
    values = admin.parse_env((root / '.env').read_text())
    assert values['RESIP_API_KEY'] != values['RESIP_BILLING_HMAC_KEY']
    assert len(values['MARZBAN_SUDO_PASSWORD']) >= 32
    output = capsys.readouterr().out
    assert all(values[key] not in output for key in ('RESIP_API_KEY', 'RESIP_BILLING_HMAC_KEY', 'MARZBAN_SUDO_PASSWORD'))


def test_snapshot_freezes_writes_and_accepts_backup_newline(configured, monkeypatch, tmp_path):
    owner, _, _ = configured
    events = []
    db = database_bytes(tmp_path)

    def call(*args, **kwargs):
        events.append(args)
        return b'container\n' if args[0] == 'ps' else b''

    def maintenance(service, code, **kwargs):
        assert ('stop', 'residential-addon') in events
        assert ('start', 'residential-addon') not in events
        return base64.b64encode(db) + b'\n' if code == admin.SQLITE_BACKUP else b'{"outbounds":[{"tag":"DIRECT"}]}'

    def execute(args, **kwargs):
        return ('a' * 40).encode() if args[0] == 'git' else ('sha256:' + 'b' * 64).encode()

    monkeypatch.setattr(owner, 'call', call)
    monkeypatch.setattr(owner, 'maintenance', maintenance)
    monkeypatch.setattr(admin, 'execute', execute)
    with owner.lock():
        source = owner.snapshot()
    admin.verify_snapshot(source)
    assert events[-1] == ('start', 'residential-addon')


def test_failed_snapshot_restarts_previously_running_controller(configured, monkeypatch):
    owner, _, _ = configured
    events = []
    def call(*args, **kwargs):
        events.append(args)
        return b'container' if args[0] == 'ps' else b''
    monkeypatch.setattr(owner, 'call', call)
    monkeypatch.setattr(owner, '_snapshot', lambda **kwargs: (_ for _ in ()).throw(admin.AdminError('backup failed')))
    with pytest.raises(admin.AdminError):
        owner.snapshot()
    assert events[-1] == ('start', 'residential-addon')


def test_snapshot_corruption_is_rejected(configured, tmp_path):
    _, config, values = configured
    source = complete_snapshot(tmp_path, config, values)
    (source / 'resip.db').write_bytes(b'corrupt')
    with pytest.raises(admin.AdminError, match='checksum'):
        admin.verify_snapshot(source)


def test_snapshot_rejects_directory_escape(configured, tmp_path):
    _, config, values = configured
    source = complete_snapshot(tmp_path, config, values)
    manifest = json.loads((source / 'manifest.json').read_text())
    manifest['sha256']['../outside'] = '0' * 64
    admin.private_write(source / 'manifest.json', json.dumps(manifest))
    with pytest.raises(admin.AdminError, match='path'):
        admin.verify_snapshot(source)


def test_tls_archive_rejects_symlinks():
    data = io.BytesIO()
    with tarfile.open(fileobj=data, mode='w') as archive:
        entry = tarfile.TarInfo('secret-link')
        entry.type = tarfile.SYMTYPE
        entry.linkname = '/etc/passwd'
        archive.addfile(entry)
    with pytest.raises(admin.AdminError, match='Unsafe'):
        admin.validate_tar(data.getvalue())


def test_restore_after_purge_stops_auto_apply(configured, monkeypatch, tmp_path):
    owner, config, values = configured
    source = complete_snapshot(tmp_path, config, values)
    (owner.root / '.env').unlink()
    (owner.root / '.resip-admin.json').unlink()
    events = []
    monkeypatch.setattr(admin, 'execute', lambda args, **kwargs: events.append(('execute', tuple(args))) or b'')
    monkeypatch.setattr(owner, 'call', lambda *args, **kwargs: events.append(('compose', args)) or b'')
    monkeypatch.setattr(owner, 'maintenance', lambda service, code, **kwargs: events.append(('maintenance', code)) or b'')
    monkeypatch.setattr(owner, 'wait_ready', lambda: None)
    owner.restore(source)
    assert admin.parse_env((owner.root / '.env').read_text())['RESIP_AUTO_APPLY'] == 'false'
    assert ('maintenance', admin.DB_RESTORE) in events
    assert ('maintenance', admin.CORE_WRITE) in events
    assert ('compose', ('up', '-d', '--no-build', '--pull', 'never')) in events
    assert (owner.state / 'restore-images.yaml').is_file()


def test_missing_image_fails_before_stopping_existing_deployment(configured, monkeypatch, tmp_path):
    owner, config, values = configured
    source = complete_snapshot(tmp_path, config, values)
    def missing(*args, **kwargs):
        raise admin.AdminError('image missing')
    monkeypatch.setattr(admin, 'execute', missing)
    monkeypatch.setattr(owner, 'call', lambda *args, **kwargs: pytest.fail('Existing deployment was changed'))
    with pytest.raises(admin.AdminError, match='image missing'):
        owner.restore(source)


def test_external_purge_requires_baseline(configured, monkeypatch):
    owner, _, _ = configured
    monkeypatch.setattr(admin, 'Admin', lambda: owner)
    monkeypatch.setattr(owner, 'preflight', lambda **kwargs: None)
    monkeypatch.setattr(owner, 'call', lambda *args, **kwargs: pytest.fail('Purge ran without a baseline'))
    assert admin.main(['remove', '--purge', '--yes']) == 1


def test_update_check_never_installs_or_stops_services(configured, monkeypatch):
    owner, _, _ = configured
    def execute(args, **kwargs):
        assert args[0] == 'git'
        if args[1] == 'rev-parse':
            return (('b' if '--verify' in args else 'a') * 40).encode()
        return b''
    monkeypatch.setattr(admin, 'Admin', lambda: owner)
    monkeypatch.setattr(admin, 'execute', execute)
    monkeypatch.setattr(owner, 'preflight', lambda **kwargs: pytest.fail('Check tried to deploy'))
    assert admin.main(['update', '--check']) == 0


def test_full_purge_rejects_baseline_with_managed_routes(configured, monkeypatch, tmp_path):
    owner, config, values = configured
    source = complete_snapshot(tmp_path, config, values)
    admin.private_write(source / 'core.json', json.dumps({'outbounds': [{'tag': 'RIP_EXIT_old', 'protocol': 'socks'}]}))
    manifest = json.loads((source / 'manifest.json').read_text())
    manifest['sha256']['core.json'] = admin.digest_file(source / 'core.json')
    admin.private_write(source / 'manifest.json', json.dumps(manifest))
    monkeypatch.setattr(admin, 'Admin', lambda: owner)
    monkeypatch.setattr(owner, 'preflight', lambda **kwargs: None)
    monkeypatch.setattr(owner, 'call', lambda *args, **kwargs: pytest.fail('Purge orphaned managed routes'))
    assert admin.main(['remove', '--purge', '--restore-core', str(source), '--yes']) == 1


def test_snapshot_rejects_unmanaged_service_images(configured, tmp_path):
    _, config, values = configured
    source = complete_snapshot(tmp_path, config, values)
    manifest = json.loads((source / 'manifest.json').read_text())
    manifest['images']['unrelated-service'] = 'sha256:' + 'c' * 64
    admin.private_write(source / 'manifest.json', json.dumps(manifest))
    with pytest.raises(admin.AdminError, match='service images'):
        admin.verify_snapshot(source)


def test_failed_update_stops_controller_and_preserves_restore_command(configured, monkeypatch, capsys):
    owner, _, _ = configured
    events = []
    def execute(args, **kwargs):
        if args[0] == 'git' and args[1] == 'rev-parse':
            return (('b' if '--verify' in args else 'a') * 40).encode()
        return b''
    def call(*args, **kwargs):
        events.append(args)
        if args[0] == 'build':
            raise admin.AdminError('simulated build failure')
        return b''
    monkeypatch.setattr(admin, 'Admin', lambda: owner)
    monkeypatch.setattr(admin, 'execute', execute)
    monkeypatch.setattr(owner, 'preflight', lambda **kwargs: None)
    monkeypatch.setattr(owner, 'snapshot', lambda **kwargs: owner.state / 'snapshots/saved')
    monkeypatch.setattr(owner, 'call', call)
    assert admin.main(['update', '--apply', '--yes']) == 1
    assert events[-1] == ('stop', 'residential-addon')
    assert '--restore-code --yes' in capsys.readouterr().err


def test_marzban_restore_db_is_readable_by_controller_group(tmp_path, monkeypatch):
    db = database_bytes(tmp_path)
    restored = tmp_path / 'restored.db'
    # Exercise the actual maintenance code without launching Docker.
    monkeypatch.setenv('RESIP_DB', str(restored))
    monkeypatch.setenv('RESIP_RESTORE_MODE', '0640')
    monkeypatch.setattr(admin.sys, 'stdin', io.TextIOWrapper(io.BytesIO(db)))
    exec(admin.DB_RESTORE, {})
    assert restored.stat().st_mode & 0o777 == 0o640
    admin.validate_database(restored.read_bytes())
