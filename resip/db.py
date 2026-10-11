import sqlite3
from contextlib import contextmanager
from pathlib import Path

SCHEMA = """
PRAGMA foreign_keys=ON;
CREATE TABLE IF NOT EXISTS exits (
 id TEXT PRIMARY KEY,
 provider TEXT NOT NULL,
 host TEXT NOT NULL,
 port INTEGER NOT NULL CHECK(port BETWEEN 1 AND 65535),
 username_env TEXT NOT NULL,
 password_env TEXT NOT NULL,
 expected_ip TEXT NOT NULL UNIQUE,
 country TEXT NOT NULL,
 supply_class TEXT NOT NULL CHECK(supply_class IN ('dedicated_residential','dedicated_isp')),
 exclusive INTEGER NOT NULL CHECK(exclusive = 1),
 enabled INTEGER NOT NULL DEFAULT 1,
 observed_ip TEXT,
 last_verified INTEGER,
 allocated_to TEXT UNIQUE,
 FOREIGN KEY(allocated_to) REFERENCES entitlements(username)
);
CREATE TABLE IF NOT EXISTS entitlements (
 username TEXT PRIMARY KEY,
 marzban_id INTEGER NOT NULL,
 exit_id TEXT NOT NULL UNIQUE,
 paid_until INTEGER NOT NULL,
 state TEXT NOT NULL CHECK(state IN ('pending','active','revoking','revoked')),
 updated INTEGER NOT NULL,
 FOREIGN KEY(exit_id) REFERENCES exits(id)
);
CREATE TABLE IF NOT EXISTS events (
 event_id TEXT PRIMARY KEY,
 payload_sha256 TEXT NOT NULL,
 created INTEGER NOT NULL
);
"""


def init(path):
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(path, timeout=30) as conn:
        conn.executescript(SCHEMA)


@contextmanager
def transaction(path):
    conn = sqlite3.connect(path, timeout=30, isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA busy_timeout=30000")
    try:
        conn.execute("BEGIN IMMEDIATE")  # Serialize allocation and webhook idempotency in SQLite.
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def select_all(conn, sql, values=()):
    return [dict(row) for row in conn.execute(sql, values).fetchall()]
