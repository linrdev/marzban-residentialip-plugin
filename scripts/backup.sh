#!/usr/bin/env bash
set -euo pipefail
# Run from repository root. Online SQLite backup; no copying a live DB file.
COMPOSE=(docker compose)
if [[ "${1:-}" == "--stack" ]]; then
  COMPOSE+=(--env-file .env -f deploy/compose.stack.yaml)
  shift
fi
DEST="${1:?Usage: bash scripts/backup.sh [--stack] /path/to/backup.db}"
umask 077
TEMP_BACKUP="$(mktemp "${DEST}.XXXXXX")"
trap 'rm -f "$TEMP_BACKUP"' EXIT
"${COMPOSE[@]}" exec -T residential-addon python -c 'import os,sqlite3,base64,tempfile,pathlib;
s=sqlite3.connect(os.environ.get("RESIP_DB","/data/resip.db"));
f=tempfile.NamedTemporaryFile();d=sqlite3.connect(f.name);s.backup(d);d.close();s.close();
print(base64.b64encode(pathlib.Path(f.name).read_bytes()).decode())' | base64 -d > "$TEMP_BACKUP"
mv "$TEMP_BACKUP" "$DEST"
echo "Backup written: $DEST"
