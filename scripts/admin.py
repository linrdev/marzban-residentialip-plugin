#!/usr/bin/env python3
"""Linux/Compose lifecycle helpers. Standard-library only; no shell env sourcing."""
import argparse
import base64
from contextlib import contextmanager
from datetime import datetime, timezone
import fcntl
import getpass
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import secrets
import shutil
import sqlite3
import subprocess
import sys
import tarfile
import tempfile
import time
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
PRESETS = {
    'existing-sqlite': ('Existing local SQLite; review and apply manually', 'sqlite', False, 60, 3600),
    'existing-mysql': ('Existing MySQL; restricted identity reader; manual apply', 'mysql', False, 60, 3600),
    'existing-sqlite-auto': ('Existing SQLite; automatic reconciliation', 'sqlite', True, 60, 3600),
    'existing-mysql-auto': ('Existing MySQL; automatic reconciliation', 'mysql', True, 60, 3600),
    'existing-strict-probe': ('Existing SQLite; 5-minute probe validity; auto apply', 'sqlite', True, 30, 300),
    'low-memory': ('Existing SQLite; 256 MB / 0.5 CPU; manual apply', 'sqlite', False, 120, 3600),
    'stack-lab': ('New Marzban + controller; private staging stack', 'stack', False, 60, 3600),
    'stack-tls': ('New stack + Caddy; panel and VLESS TLS hostnames', 'stack-tls', False, 60, 3600),
}
MANAGED_FILES = ['.env', '.resip-admin.json', 'compose.yaml', 'compose.mysql.yaml',
                 'compose.admin.yaml', 'deploy/compose.stack.yaml', 'deploy/Caddyfile',
                 'deploy/xray_config.json']
SQLITE_BACKUP = """import os,sqlite3,base64,tempfile,pathlib
p=pathlib.Path(os.environ.get('RESIP_DB','/data/resip.db'))
assert p.is_file(), 'State database not found'
s=sqlite3.connect(str(p)); f=tempfile.NamedTemporaryFile(); d=sqlite3.connect(f.name)
s.backup(d); d.close(); s.close()
print(base64.b64encode(pathlib.Path(f.name).read_bytes()).decode())
"""
CORE_READ = "from resip.settings import Settings; from resip.marzban import Marzban; import json; m=Marzban(Settings.from_env()); print(json.dumps(m.config())); m.close()"
CORE_WRITE = "from resip.settings import Settings; from resip.marzban import Marzban; import json,sys; m=Marzban(Settings.from_env()); desired=json.load(sys.stdin); m.put_config(desired); assert m.config()==desired; m.close()"
DB_RESTORE = """import os,pathlib,sqlite3,sys,tempfile
p=pathlib.Path(os.environ.get('RESIP_DB','/data/resip.db'))
fd,name=tempfile.mkstemp(dir=p.parent); os.close(fd); tmp=pathlib.Path(name)
tmp.write_bytes(sys.stdin.buffer.read()); tmp.chmod(int(os.environ.get('RESIP_RESTORE_MODE','0600'),8))
c=sqlite3.connect(name); assert c.execute('PRAGMA quick_check').fetchone()[0]=='ok'; c.close()
for suffix in ('-wal','-shm'): pathlib.Path(str(p)+suffix).unlink(missing_ok=True)
os.replace(tmp,p)
"""


class AdminError(RuntimeError):
    pass


def execute(args, *, cwd=ROOT, data=None, sensitive=False, check=True):
    result = subprocess.run(args, cwd=cwd, input=data, capture_output=True, timeout=900)
    if check and result.returncode:
        detail = 'Sensitive operation failed; inspect the service locally.' if sensitive else result.stderr.decode(errors='replace').strip()
        raise AdminError(f'{Path(args[0]).name} failed ({result.returncode}): {detail}')
    return result.stdout


def private_write(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp = tempfile.mkstemp(prefix='.resip-', dir=path.parent)
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, 'wb') as output:
            output.write(data.encode() if isinstance(data, str) else data)
        os.replace(temp, path)
    finally:
        Path(temp).unlink(missing_ok=True)


def parse_env(text):
    """Literal single-line dotenv subset, including escaped single quotes."""
    result = {}
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith('#'):
            continue
        match = re.fullmatch(r'(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)=(.*)', line)
        if not match:
            raise AdminError('Use single-line KEY=value entries in .env; the file is never executed.')
        key, value = match.groups()
        if value.startswith("'"):
            quoted = re.fullmatch(r"'((?:\\'|[^'])*)'\s*(?:#.*)?", value)
            if not quoted:
                raise AdminError('Invalid single-quoted dotenv value')
            value = quoted.group(1).replace("\\'", "'")
        elif value.startswith('"'):
            # JSON-compatible double quotes; prefer this wizard's single quotes.
            try:
                value = json.loads(value)
            except ValueError as exc:
                raise AdminError('Use literal single quotes for special characters in .env') from exc
        else:
            value = value.split(' #', 1)[0].rstrip()
        result[key] = value
    return result


def format_env(values):
    for value in values.values():
        if '\n' in value or '\r' in value:
            raise AdminError('Multiline environment values are unsupported')
    return '\n'.join(key + "='" + value.replace("'", "\\'") + "'" for key, value in sorted(values.items())) + '\n'


class Admin:
    def __init__(self, root=ROOT):
        self.root = Path(root)
        self.state = self.root / '.resip'
        self.archive_images = False

    def settings(self):
        path = self.root / '.resip-admin.json'
        cfg = json.loads(path.read_text()) if path.exists() else {'version': 1, 'preset': 'existing-sqlite', 'project': self.root.name.lower()}
        if cfg.get('preset') not in PRESETS or not re.fullmatch(r'[a-z0-9][a-z0-9_-]{0,62}', cfg.get('project', '')):
            raise AdminError('Invalid preset or Compose project name in admin configuration')
        return cfg

    def compose(self, *, override=True):
        cfg = self.settings()
        mode = PRESETS[cfg['preset']][1]
        args = ['docker', 'compose', '--project-name', cfg['project'], '--env-file', str(self.root / '.env')]
        files = ['deploy/compose.stack.yaml'] if mode.startswith('stack') else ['compose.yaml']
        if mode == 'mysql':
            files.append('compose.mysql.yaml')
        if (self.root / 'compose.admin.yaml').exists():
            files.append('compose.admin.yaml')
        if override and (self.state / 'restore-images.yaml').exists():
            files.append('.resip/restore-images.yaml')
        for file in files:
            args += ['-f', str(self.root / file)]
        if mode == 'stack-tls':
            args += ['--profile', 'tls']
        return args

    def call(self, *args, **kwargs):
        override = kwargs.pop('override', True)
        return execute([*self.compose(override=override), *args], cwd=self.root, **kwargs)

    @contextmanager
    def lock(self):
        self.state.mkdir(mode=0o700, exist_ok=True)
        self.state.chmod(0o700)
        with (self.state / 'operations.lock').open('a') as handle:
            os.chmod(handle.name, 0o600)
            try:
                fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as exc:
                raise AdminError('Another install/update/remove operation is running') from exc
            yield

    def preflight(self, *, config=True):
        for binary in ('docker', 'git'):
            if not shutil.which(binary):
                raise AdminError(f'Install {binary} first; see docs/linux-admin.md')
        version = execute(['docker', 'compose', 'version', '--short'], cwd=self.root).decode().strip()
        match = re.search(r'(\d+)\.(\d+)\.(\d+)', version)
        if not match or tuple(map(int, match.groups())) < (2, 24, 4):
            raise AdminError('Docker Compose >=2.24.4 is required')
        execute(['docker', 'info', '--format', '{{.ServerVersion}}'], cwd=self.root)
        if not config:
            return
        if not (self.root / '.env').is_file():
            raise AdminError('Run bash wizard.sh first, or create a private .env from .env.example')
        self.call('config', '--quiet', sensitive=True)

    def environment(self):
        config = json.loads(self.call('config', '--format', 'json', sensitive=True))
        return config['services']['residential-addon']['environment']

    def validate_credentials(self):
        env = self.environment()
        for key in ('RESIP_API_KEY', 'RESIP_BILLING_HMAC_KEY', 'MARZBAN_SUDO_USER', 'MARZBAN_SUDO_PASSWORD'):
            if not env.get(key) or str(env[key]).startswith('REPLACE_'):
                raise AdminError(f'Configure {key} before installing')
        if min(len(env['RESIP_API_KEY']), len(env['RESIP_BILLING_HMAC_KEY'])) < 24 or env['RESIP_API_KEY'] == env['RESIP_BILLING_HMAC_KEY']:
            raise AdminError('Use distinct admin/HMAC secrets of at least 24 characters')

    def wait_ready(self, seconds=90):
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            try:
                with urllib.request.urlopen('http://127.0.0.1:8787/readyz', timeout=3) as response:
                    if response.status == 200:
                        return
            except OSError:
                pass
            time.sleep(1)
        raise AdminError('Controller did not become locally ready; inspect Compose logs')

    def maintenance(self, service, code, *, data=None, env=None):
        args = ['run', '--rm', '--no-deps', '-T']
        for key, value in (env or {}).items():
            args += ['-e', f'{key}={value}']
        args += [service, 'python', '-c', code]
        return self.call(*args, data=data, sensitive=True)

    def snapshot(self, *, label='manual'):
        """Freeze controller writes while pairing state and core snapshots."""
        running = bool(self.call('ps', '--status', 'running', '-q', 'residential-addon').strip())
        if running:
            self.call('stop', 'residential-addon')
        try:
            return self._snapshot(label=label)
        finally:
            if running:
                self.call('start', 'residential-addon')

    def _snapshot(self, *, label):
        """SQLite backup API; credentials/core config never enter terminal output."""
        if not (self.root / '.resip-admin.json').exists():
            private_write(self.root / '.resip-admin.json', json.dumps(self.settings(), indent=2) + '\n')
        name = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ') + '-' + secrets.token_hex(3)
        dest = self.state / 'snapshots' / name
        dest.mkdir(parents=True, mode=0o700)
        mode = PRESETS[self.settings()['preset']][1]
        for relative in MANAGED_FILES:
            source = self.root / relative
            if source.is_file():
                private_write(dest / 'files' / relative, source.read_bytes())
        # Both commands work with a stopped controller; no Uvicorn is started.
        db = base64.b64decode(self.maintenance('residential-addon', SQLITE_BACKUP).strip(), validate=True)
        validate_database(db)
        private_write(dest / 'resip.db', db)
        if mode.startswith('stack'):
            core = self.maintenance('marzban', "import pathlib; print(pathlib.Path('/var/lib/marzban/xray_config.json').read_text())")
            mdb = base64.b64decode(self.maintenance('marzban', SQLITE_BACKUP,
                                                  env={'RESIP_DB': '/var/lib/marzban/db.sqlite3'}).strip(), validate=True)
            validate_database(mdb)
            private_write(dest / 'marzban.db', mdb)
        else:
            core = self.maintenance('residential-addon', CORE_READ)
        json.loads(core)
        private_write(dest / 'core.json', core)
        if mode == 'stack-tls':
            for volume in ('data', 'config'):
                archive = self.call('run', '--rm', '--no-deps', '-T', 'caddy', 'tar', '-C', '/' + volume, '-cf', '-', '.', sensitive=True)
                validate_tar(archive)
                private_write(dest / ('caddy-' + volume + '.tar'), archive)
        images = {}
        for service in (['residential-addon', 'marzban', 'caddy'] if mode == 'stack-tls' else
                        ['residential-addon', 'marzban'] if mode == 'stack' else ['residential-addon']):
            container = self.call('ps', '-a', '-q', service).decode().strip()
            if container:
                if '\n' in container:
                    raise AdminError('Multiple service containers found; refuse ambiguous snapshot')
                images[service] = execute(['docker', 'inspect', '--format', '{{.Image}}', container], cwd=self.root).decode().strip()
        if mode.startswith('stack') and 'residential-addon' in images:
            images['storage-init'] = images['residential-addon']
        if self.archive_images and images:
            archive = dest / 'images.tar'
            private_write(archive, b'')
            execute(['docker', 'image', 'save', '--output', str(archive), *sorted(set(images.values()))], cwd=self.root, sensitive=True)
            archive.chmod(0o600)
        sha = execute(['git', 'rev-parse', 'HEAD'], cwd=self.root).decode().strip()
        checksums = {str(p.relative_to(dest)): digest_file(p)
                     for p in dest.rglob('*') if p.is_file()}
        private_write(dest / 'manifest.json', json.dumps({'version': 1, 'label': label, 'git_sha': sha,
                                                        'settings': self.settings(), 'images': images,
                                                        'sha256': checksums}, indent=2) + '\n')
        print(f'Complete private snapshot: {dest}')
        return dest

    def restore(self, source, *, restore_code=False):
        source, manifest = verify_snapshot(source)
        cfg = manifest['settings']
        configured = (self.root / '.env').exists() and (self.root / '.resip-admin.json').exists()
        if configured and cfg['project'] != self.settings()['project']:
            raise AdminError('Snapshot belongs to another Compose project; configure that project first')
        images = manifest['images']
        if 'residential-addon' not in images:
            raise AdminError('Snapshot lacks the installed image')
        for image in images.values():
            if not re.fullmatch(r'sha256:[a-f0-9]{64}', image):
                raise AdminError('Invalid saved image ID')
        if (source / 'images.tar').exists():
            execute(['docker', 'image', 'load', '--input', str(source / 'images.tar')], cwd=self.root, sensitive=True)
        # Fail before stopping/changing deployment when required images are absent.
        for image in images.values():
            execute(['docker', 'image', 'inspect', image], cwd=self.root)
        if restore_code:
            ensure_clean_git(self.root)
            execute(['git', 'cat-file', '-e', manifest['git_sha'] + '^{commit}'], cwd=self.root)
        # Snapshot the present deployment before destructive restoration, if it exists.
        if configured and self.call('ps', '-a', '-q', 'residential-addon').strip():
            self.snapshot(label='before-restore')
        if configured:
            self.call('stop')
        if restore_code:
            execute(['git', 'switch', '--detach', '--quiet', manifest['git_sha']], cwd=self.root)
        for relative in MANAGED_FILES:
            saved = source / 'files' / relative
            if saved.is_file():
                private_write(self.root / relative, saved.read_bytes())
            elif relative == 'compose.admin.yaml':
                (self.root / relative).unlink(missing_ok=True)
        private_write(self.state / 'restore-images.yaml', json.dumps({'services': {name: {'image': image} for name, image in images.items()}}, indent=2))
        self.maintenance('residential-addon', DB_RESTORE, data=(source / 'resip.db').read_bytes())
        mode = PRESETS[cfg['preset']][1]
        if mode.startswith('stack'):
            self.maintenance('marzban', "import pathlib,os; p=pathlib.Path('/var/lib/marzban'); p.mkdir(exist_ok=True); os.chown(p,0,10001); p.chmod(0o2770)")
            self.maintenance('marzban', DB_RESTORE, data=(source / 'marzban.db').read_bytes(), env={'RESIP_DB': '/var/lib/marzban/db.sqlite3', 'RESIP_RESTORE_MODE': '0640'})
            self.maintenance('marzban', "import pathlib,sys; p=pathlib.Path('/var/lib/marzban/xray_config.json'); p.write_bytes(sys.stdin.buffer.read()); p.chmod(0o660)", data=(source / 'core.json').read_bytes())
        else:
            self.maintenance('residential-addon', CORE_WRITE, data=(source / 'core.json').read_bytes())
        if mode == 'stack-tls':
            for volume in ('data', 'config'):
                self.call('run', '--rm', '--no-deps', '-T', 'caddy', 'tar', '-C', '/' + volume, '-xf', '-',
                          data=(source / ('caddy-' + volume + '.tar')).read_bytes(), sensitive=True)
        # Require review before automatic core writes resume after restoration.
        env = parse_env((self.root / '.env').read_text())
        env['RESIP_AUTO_APPLY'] = 'false'
        private_write(self.root / '.env', format_env(env))
        self.call('up', '-d', '--no-build', '--pull', 'never')
        self.wait_ready()
        print('Snapshot restored. Auto-apply is disabled; review /v1/status and a dry-run before enabling it.')


def validate_database(data):
    with tempfile.TemporaryDirectory() as temp:
        p = Path(temp) / 'check.db'
        p.write_bytes(data)
        with sqlite3.connect(p) as conn:
            if conn.execute('PRAGMA quick_check').fetchone()[0] != 'ok':
                raise AdminError('Snapshot SQLite integrity check failed')


def digest_file(path):
    digest = hashlib.sha256()
    with Path(path).open('rb') as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b''):
            digest.update(block)
    return digest.hexdigest()


def validate_tar(data):
    import io
    with tarfile.open(fileobj=io.BytesIO(data)) as archive:
        for item in archive:
            path = PurePosixPath(item.name)
            if path.is_absolute() or '..' in path.parts or not (item.isfile() or item.isdir()):
                raise AdminError('Unsafe path/link/device in snapshot archive')


def verify_snapshot(source):
    source = Path(source).resolve()
    manifest = json.loads((source / 'manifest.json').read_text())
    if manifest.get('version') != 1 or manifest['settings'].get('preset') not in PRESETS:
        raise AdminError('Unsupported snapshot manifest')
    if not re.fullmatch(r'[a-f0-9]{40}', manifest.get('git_sha', '')):
        raise AdminError('Invalid snapshot commit')
    if not re.fullmatch(r'[a-z0-9][a-z0-9_-]{0,62}', manifest['settings'].get('project', '')):
        raise AdminError('Invalid snapshot project')
    if not isinstance(manifest.get('images'), dict) or not set(manifest['images']) <= {'residential-addon', 'marzban', 'caddy', 'storage-init'}:
        raise AdminError('Invalid snapshot service images')
    required = {'resip.db', 'core.json', 'files/.env', 'files/.resip-admin.json'}
    mode = PRESETS[manifest['settings']['preset']][1]
    if mode.startswith('stack'):
        required.add('marzban.db')
    if mode == 'stack-tls':
        required.update({'caddy-data.tar', 'caddy-config.tar'})
    if not required <= manifest['sha256'].keys():
        raise AdminError('Incomplete snapshot')
    for relative, digest in manifest['sha256'].items():
        path = source / relative
        if not path.resolve().is_relative_to(source) or path.is_symlink() or not path.is_file():
            raise AdminError('Invalid snapshot path')
        if digest_file(path) != digest:
            raise AdminError('Snapshot checksum mismatch')
        if relative.endswith('.tar') and relative != 'images.tar':
            validate_tar(path.read_bytes())
    if json.loads((source / 'files/.resip-admin.json').read_text()) != manifest['settings']:
        raise AdminError('Snapshot settings do not match its manifest')
    validate_database((source / 'resip.db').read_bytes())
    if mode.startswith('stack'):
        validate_database((source / 'marzban.db').read_bytes())
    core = json.loads((source / 'core.json').read_text())
    if not isinstance(core, dict) or not isinstance(core.get('outbounds'), list):
        raise AdminError('Invalid core snapshot')
    return source, manifest


def ensure_clean_git(root):
    if execute(['git', 'status', '--porcelain', '--untracked-files=all'], cwd=root).strip():
        raise AdminError('Git checkout has local changes; commit/stash them before updating/restoring code')


def confirm(text, yes):
    if yes:
        return
    if not sys.stdin.isatty() or input(text + ' [y/N] ').strip().lower() not in ('y', 'yes'):
        raise AdminError('Cancelled; use --yes only after reviewing the operation')


def wizard(admin, args):
    preset = args.preset
    if not preset:
        for number, (name, info) in enumerate(PRESETS.items(), 1):
            print(f'{number}. {name}: {info[0]}')
        selection = input('Preset number [1]: ').strip() or '1'
        if not selection.isdigit() or not 1 <= int(selection) <= len(PRESETS):
            raise AdminError('Invalid preset selection')
        preset = list(PRESETS)[int(selection) - 1]
    description, mode, automatic, poll, age = PRESETS[preset]
    if args.dry_run:
        print(f'Would configure {preset}: {description}; auto-apply={automatic}; poll={poll}s; probe-age={age}s')
        return
    env_path = admin.root / '.env'
    values = parse_env(env_path.read_text() if env_path.exists() else (admin.root / '.env.example').read_text())
    for key in tuple(values):
        if key in os.environ:
            values[key] = os.environ[key]
    def value(key, prompt, default='', secret=False, required=True):
        old = values.get(key, default)
        if old.startswith('REPLACE_') or old.endswith('.example.net') or old == 'operator@example.net':
            old = default
        if args.non_interactive:
            answer = old
        else:
            answer = getpass.getpass(prompt + ' [enter keeps existing]: ') if secret else input(f'{prompt} [{old}]: ')
            answer = answer or old
        if required and not answer:
            raise AdminError(f'Set {key} in the environment or answer the wizard')
        values[key] = answer
    for key in ('RESIP_API_KEY', 'RESIP_BILLING_HMAC_KEY'):
        if not values.get(key) or values[key].startswith('REPLACE_'):
            values[key] = secrets.token_urlsafe(48)
    value('MARZBAN_SUDO_USER', 'Marzban sudo username', 'resip_operator')
    if mode.startswith('stack'):
        if not values.get('MARZBAN_SUDO_PASSWORD') or values['MARZBAN_SUDO_PASSWORD'].startswith('REPLACE_'):
            values['MARZBAN_SUDO_PASSWORD'] = secrets.token_urlsafe(48)
        values['MARZBAN_URL'] = 'http://marzban:8000'
        values['RESIP_ALLOW_HTTP_MARZBAN'] = 'true'
    else:
        value('MARZBAN_URL', 'Marzban HTTPS management URL')
        if not values['MARZBAN_URL'].startswith('https://'):
            raise AdminError('Wizard presets require HTTPS for external Marzban; private HTTP is an explicit manual setting')
        values['RESIP_ALLOW_HTTP_MARZBAN'] = 'false'
        value('MARZBAN_SUDO_PASSWORD', 'Existing Marzban sudo password', secret=True)
        if mode == 'mysql':
            previous = values.get('MARZBAN_DB_URL', '')
            if not previous.startswith('mysql+pymysql://'):
                values['MARZBAN_DB_URL'] = ''
            value('MARZBAN_DB_URL', 'Read-only live MySQL URI (password URL-encoded)', secret=True)
            if not values['MARZBAN_DB_URL'].startswith('mysql+pymysql://'):
                raise AdminError('MySQL preset requires a mysql+pymysql:// identity URI')
        else:
            value('MARZBAN_DATA_DIR', 'Live Marzban data directory', '/var/lib/marzban')
            values['MARZBAN_DB_URL'] = 'sqlite:///file:/marzban-ro/db.sqlite3?mode=ro&uri=true'
    if mode == 'stack-tls':
        value('PANEL_DOMAIN', 'Panel hostname (will have a public TLS route)')
        value('VPN_DOMAIN', 'VPN TLS hostname')
        value('ACME_EMAIL', 'ACME certificate email')
    if not args.non_interactive:
        value('PROXY_ONE_USERNAME', 'Optional first fixed proxy username', required=False)
        value('PROXY_ONE_PASSWORD', 'Optional first fixed proxy password', secret=True, required=False)
    values.update(RESIP_AUTO_APPLY=str(automatic).lower(), RESIP_POLL_SECONDS=str(poll), RESIP_PROBE_MAX_AGE=str(age))
    project = args.project or ('marzban-residential' if mode.startswith('stack') else admin.root.name.lower())
    if not re.fullmatch(r'[a-z0-9][a-z0-9_-]{0,62}', project):
        raise AdminError('Use a lowercase Compose project name containing letters/digits/_/-')
    print(f'Preset: {preset}; Compose project: {project}; automatic core apply: {automatic}')
    if automatic:
        print('Automatic configuration changes can restart Marzban and all connected nodes.')
    if mode == 'stack-tls':
        print('TLS profile publishes the panel hostname; restrict management access before installation.')
    confirm('Save configuration (current settings are preserved in private config history)?', args.yes)
    with admin.lock():
        if env_path.exists():
            history = admin.state / 'config-history' / (datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ') + '-' + secrets.token_hex(2))
            private_write(history / '.env', env_path.read_bytes())
            if (admin.root / '.resip-admin.json').exists():
                private_write(history / '.resip-admin.json', (admin.root / '.resip-admin.json').read_bytes())
        private_write(env_path, format_env(values))
        private_write(admin.root / '.resip-admin.json', json.dumps({'version': 1, 'preset': preset, 'project': project}, indent=2) + '\n')
        override = admin.root / 'compose.admin.yaml'
        if preset == 'low-memory':
            private_write(override, json.dumps({'services': {'residential-addon': {'mem_limit': '256m', 'cpus': 0.5}}}, indent=2))
        else:
            override.unlink(missing_ok=True)
    print('Configuration saved privately. Review it, then run bash installer.sh.')


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='command', required=True)
    wiz = sub.add_parser('wizard', help='Configure one of eight Linux/Compose presets')
    wiz.add_argument('--preset', choices=PRESETS)
    wiz.add_argument('--project')
    wiz.add_argument('--non-interactive', action='store_true')
    install = sub.add_parser('install', help='Validate configuration and install the selected Compose stack')
    update = sub.add_parser('update', help='Check Git updates by default; apply only with --apply')
    update.add_argument('--apply', action='store_true')
    update.add_argument('--check', action='store_true')
    update.add_argument('--snapshot-only', action='store_true')
    update.add_argument('--cron-example', action='store_true')
    update.add_argument('--ref', default='origin/main')
    remove = sub.add_parser('remove', help='Remove managed Compose runtime or restore a private snapshot')
    remove.add_argument('--purge', action='store_true', help='Delete this project\'s volumes, including bundled Marzban')
    remove.add_argument('--delete-config', action='store_true', help='Remove .env/admin config after a successful snapshot')
    remove.add_argument('--restore', metavar='SNAPSHOT')
    remove.add_argument('--restore-code', action='store_true', help='Also switch clean Git checkout to the snapshot commit')
    remove.add_argument('--restore-core', metavar='SNAPSHOT', help='Restore saved upstream core before controller removal')
    for item in (wiz, install, update, remove):
        item.add_argument('--yes', action='store_true')
        item.add_argument('--dry-run', action='store_true')
        item.add_argument('--include-images', action='store_true', help='Also archive local image bytes in snapshots (can use GBs of disk)')
    args = parser.parse_args(argv)
    admin = Admin()
    admin.archive_images = args.include_images
    try:
        if args.command == 'wizard':
            wizard(admin, args)
            return 0
        if args.command == 'update' and args.cron_example:
            print('See docs/linux-admin.md for daily check and scheduled snapshot examples.\n'
                  '17 4 * * * cd /opt/marzban-residentialip-plugin && /bin/bash updater.sh --check >> /var/log/resip-update.log 2>&1\n'
                  '37 3 * * 0 cd /opt/marzban-residentialip-plugin && /bin/bash updater.sh --snapshot-only --yes >> /var/log/resip-backup.log 2>&1')
            return 0
        if args.dry_run:
            print('Would run ' + args.command + ' for ' + admin.settings()['project'])
            print('Compose argv: ' + json.dumps(admin.compose()))
            if args.command == 'remove':
                print(f'purge={args.purge}; restore={bool(args.restore)}; restore-core={bool(args.restore_core)}')
            return 0
        if args.command == 'update' and not args.snapshot_only:
            if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_./-]*', args.ref):
                raise AdminError('Invalid Git ref')
            execute(['git', 'fetch', '--prune', 'origin'], cwd=admin.root)
            current = execute(['git', 'rev-parse', 'HEAD'], cwd=admin.root).decode().strip()
            target = execute(['git', 'rev-parse', '--verify', args.ref + '^{commit}'], cwd=admin.root).decode().strip()
            print(f'Installed code: {current[:12]}; available code: {target[:12]}')
            if args.check or not args.apply or current == target:
                print('Update available; run updater.sh --apply during a maintenance window.' if current != target else 'Code is current. Image/security freshness is a separate check.')
                return 0
        restoring = args.command == 'remove' and bool(args.restore)
        admin.preflight(config=not restoring)
        with admin.lock():
            if args.command == 'install':
                admin.validate_credentials()
                confirm('Install/start the selected Compose project?', args.yes)
                private_write(admin.root / '.resip-admin.json', json.dumps(admin.settings(), indent=2) + '\n')
                if admin.call('ps', '-a', '-q', 'residential-addon').strip():
                    admin.snapshot(label='before-install')
                admin.call('up', '-d', '--build')
                admin.wait_ready()
                admin.snapshot(label='installed-baseline')
                print('Installed; baseline snapshot saved. Review a dry-run before granting real entitlements.')
            elif args.command == 'update':
                if args.snapshot_only:
                    admin.snapshot(label='scheduled-backup')
                    return 0
                ensure_clean_git(admin.root)
                execute(['git', 'merge-base', '--is-ancestor', current, target], cwd=admin.root)
                confirm('Snapshot, fast-forward code, rebuild, and restart this project?', args.yes)
                saved = admin.snapshot(label='before-update')
                try:
                    execute(['git', 'merge', '--ff-only', target], cwd=admin.root)
                    admin.call('build', override=False)
                    (admin.state / 'restore-images.yaml').unlink(missing_ok=True)
                    admin.call('up', '-d', '--build')
                    admin.wait_ready()
                except Exception as exc:
                    admin.call('stop', 'residential-addon', check=False)
                    raise AdminError(f'Update failed; controller stopped to avoid further changes. Restore explicitly: bash remove.sh --restore {saved} --restore-code --yes') from exc
                print('Update applied. Check /v1/status and actual subscriber egress.')
            else:
                if args.restore:
                    if args.purge or args.delete_config or args.restore_core:
                        raise AdminError('--restore cannot be combined with removal flags')
                    confirm('Restore DB, saved images/config/core and restart (billing must be paused)?', args.yes)
                    admin.restore(args.restore, restore_code=args.restore_code)
                    return 0
                if args.restore_code:
                    raise AdminError('--restore-code requires --restore')
                mode = PRESETS[admin.settings()['preset']][1]
                if args.purge and not mode.startswith('stack') and not args.restore_core:
                    raise AdminError('Purging an external-Marzban add-on requires --restore-core SNAPSHOT so managed routes are not orphaned')
                core = None
                if args.restore_core:
                    source, manifest = verify_snapshot(args.restore_core)
                    if manifest['settings']['project'] != admin.settings()['project']:
                        raise AdminError('Core snapshot belongs to another project')
                    core = (source / 'core.json').read_bytes()
                    parsed = json.loads(core)
                    if args.purge and not mode.startswith('stack') and (
                        any(str(o.get('tag', '')).startswith('RIP_') for o in parsed['outbounds']) or
                        any(str(r.get('outboundTag', '')).startswith('RIP_') for r in parsed.get('routing', {}).get('rules', []))):
                        raise AdminError('For full external-addon removal, select a baseline core without RIP_ routes')
                confirm('Snapshot and remove this Compose project' + (' INCLUDING ALL ITS VOLUMES' if args.purge else ' (preserve volumes)') + '?', args.yes)
                saved = admin.snapshot(label='before-removal')
                admin.call('stop', 'residential-addon')
                if core is not None:
                    print('Restoring saved core; disable premium subscribers first if its routing returns them to standard egress.')
                    admin.maintenance('residential-addon', CORE_WRITE, data=core)
                admin.call('down', '--remove-orphans', *(['--volumes'] if args.purge else []))
                if args.delete_config:
                    for name in ('.env', '.resip-admin.json', 'compose.admin.yaml'):
                        (admin.root / name).unlink(missing_ok=True)
                    (admin.state / 'restore-images.yaml').unlink(missing_ok=True)
                print(f'Removed managed runtime; private snapshot retained at {saved}. Host Docker/Git and source files remain.')
        return 0
    except (AdminError, OSError, ValueError, KeyError, sqlite3.Error, EOFError, subprocess.TimeoutExpired) as exc:
        print('Error: ' + str(exc), file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
