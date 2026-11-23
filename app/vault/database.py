from contextlib import closing, contextmanager
from pathlib import Path
import sqlite3


class Vault:
    def __init__(self, path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.session() as conn:
            conn.execute('CREATE TABLE IF NOT EXISTS volumes (id TEXT PRIMARY KEY, manifest TEXT NOT NULL, evidence TEXT NOT NULL)')
            conn.execute('CREATE TABLE IF NOT EXISTS slips (id TEXT PRIMARY KEY REFERENCES volumes(id), digest TEXT NOT NULL, body TEXT NOT NULL)')
            conn.execute('CREATE TABLE IF NOT EXISTS closes (period TEXT PRIMARY KEY, body TEXT NOT NULL, closed_at TEXT NOT NULL)')
            conn.execute(
                'CREATE TABLE IF NOT EXISTS postings ('
                'entry TEXT PRIMARY KEY, target TEXT NOT NULL, period TEXT NOT NULL, '
                'source_date TEXT NOT NULL, posted_at TEXT NOT NULL, pages INTEGER NOT NULL, '
                'run_id TEXT NOT NULL, line INTEGER)')
            conn.execute('CREATE INDEX IF NOT EXISTS postings_period ON postings (period)')

    @contextmanager
    def session(self):
        with closing(sqlite3.connect(self.path,timeout=10)) as conn:
            conn.execute('PRAGMA foreign_keys=ON')
            with conn:
                yield conn
