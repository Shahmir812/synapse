"""SQLite history and durable upload state, scoped by project and conversation."""
from contextlib import contextmanager
from datetime import datetime, UTC
import json
import os
from pathlib import Path
import sqlite3
from uuid import uuid4


class LocalMemoryError(Exception):
    pass


def database_path():
    return Path(os.environ.get('SYNAPSE_MEMORY_DB') or Path.home() / '.synapse' / 'memory.sqlite3').expanduser()


@contextmanager
def database():
    connection = None
    try:
        path = database_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(path, timeout=5)
        connection.row_factory = sqlite3.Row
        connection.executescript('''
            CREATE TABLE IF NOT EXISTS conversations (
                scope TEXT NOT NULL, session TEXT NOT NULL, cached TEXT NOT NULL DEFAULT '[]',
                PRIMARY KEY(scope, session));
            CREATE TABLE IF NOT EXISTS exchanges (
                seq INTEGER PRIMARY KEY AUTOINCREMENT, id TEXT NOT NULL UNIQUE,
                scope TEXT NOT NULL, session TEXT NOT NULL,
                question TEXT NOT NULL, answer TEXT NOT NULL, created TEXT NOT NULL,
                state TEXT NOT NULL DEFAULT 'pending' CHECK(state IN ('pending','uploading','synced')));
            CREATE INDEX IF NOT EXISTS exchanges_scope ON exchanges(scope, session, seq);
        ''')
        yield connection
        connection.commit()
    except (OSError, sqlite3.Error, ValueError) as error:
        if connection:
            connection.rollback()
        raise LocalMemoryError('Cannot read or save local memory. Check SYNAPSE_MEMORY_DB and disk permissions.') from error
    finally:
        if connection:
            connection.close()


def remember_session(scope, session):
    with database() as db:
        db.execute('INSERT OR IGNORE INTO conversations(scope,session) VALUES (?,?)', (scope, session))


def known(scope, session):
    with database() as db:
        return db.execute('SELECT 1 FROM conversations WHERE scope=? AND session=?', (scope, session)).fetchone() is not None


def sessions(scope):
    with database() as db:
        return [row[0] for row in db.execute('SELECT session FROM conversations WHERE scope=? ORDER BY rowid DESC', (scope,))]


def append(scope, session, question, answer):
    with database() as db:
        db.execute('INSERT OR IGNORE INTO conversations(scope,session) VALUES (?,?)', (scope, session))
        db.execute('INSERT INTO exchanges(id,scope,session,question,answer,created) VALUES (?,?,?,?,?,?)',
                   (uuid4().hex, scope, session, question, answer, datetime.now(UTC).isoformat()))
        cached = json.loads(db.execute('SELECT cached FROM conversations WHERE scope=? AND session=?', (scope, session)).fetchone()[0])
        cached += [{'role':'user', 'content':question}, {'role':'assistant', 'content':answer}]
        # The full exchanges remain in SQLite; only the prompt cache is bounded.
        from synapse.memory import _bounded
        db.execute('UPDATE conversations SET cached=? WHERE scope=? AND session=?', (json.dumps(_bounded(cached)), scope, session))


def context(scope, session):
    with database() as db:
        row = db.execute('SELECT cached FROM conversations WHERE scope=? AND session=?', (scope, session)).fetchone()
        return json.loads(row[0]) if row else []


def cache_context(scope, session, messages):
    with database() as db:
        db.execute("UPDATE conversations SET cached=? WHERE scope=? AND session=? AND NOT EXISTS (SELECT 1 FROM exchanges WHERE scope=? AND session=? AND state!='synced')", (json.dumps(messages), scope, session, scope, session))


def pending(scope, session=None):
    with database() as db:
        if session is None:
            return [dict(row) for row in db.execute("SELECT * FROM exchanges WHERE scope=? AND state!='synced' ORDER BY seq", (scope,))]
        return [dict(row) for row in db.execute("SELECT * FROM exchanges WHERE scope=? AND session=? AND state!='synced' ORDER BY seq", (scope, session))]


def set_state(exchange_id, state):
    with database() as db:
        db.execute('UPDATE exchanges SET state=? WHERE id=?', (state, exchange_id))


@contextmanager
def sync_lock():
    """Serialize sync workers sharing this database, including separate processes."""
    import fcntl
    try:
        path = database_path().with_suffix('.sync.lock')
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open('a') as lock:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                yield False
                return
            try:
                yield True
            finally:
                fcntl.flock(lock, fcntl.LOCK_UN)
    except OSError as error:
        raise LocalMemoryError('Cannot acquire the local memory synchronization lock.') from error
