from contextlib import contextmanager
from types import SimpleNamespace
import subprocess
import sys

import pytest

from synapse import memory, local_memory as local
from synapse.project_registry import create_project
from test_memory import backend


@contextmanager
def offline(scope):
    raise memory.MemoryError('offline')
    yield


def test_offline_history_survives_restart_and_syncs_in_order(backend, monkeypatch):
    create_project('Offline')
    remote = memory.honcho_client
    monkeypatch.setattr(memory, 'honcho_client', offline)
    c = memory.open_conversation()
    c.save('First question', 'First answer')
    c.save('Second question', 'Second answer')
    assert len(local.pending(c.scope)) == 2
    result = subprocess.run([sys.executable, '-c',
        f'from synapse.local_memory import context; print(context({c.scope!r}, {c.id!r}))'], capture_output=True, text=True)
    assert result.returncode == 0 and 'First answer' in result.stdout
    resumed = memory.open_conversation(c.id)
    assert len(resumed.context('Continue')) == 4
    assert memory.conversations()[0] == [c.id]
    monkeypatch.setattr(memory, 'honcho_client', remote)
    assert 'sync complete' in memory.sync_pending(c.scope)
    assert local.pending(c.scope) == []
    stored = backend[0][(c.scope, c.id)]
    assert [m['content'] for m in stored] == ['First question','First answer','Second question','Second answer']
    memory.sync_pending(c.scope)
    assert len(stored) == 4


def test_timeout_after_server_save_is_reconciled(backend, monkeypatch):
    create_project('Timeout')
    c = memory.open_conversation()
    remote = memory.honcho_client
    @contextmanager
    def uncertain(scope):
        with remote(scope) as client:
            original = client.session
            def session(*args, **kwargs):
                result = original(*args, **kwargs)
                add = result.add_messages
                def timeout(messages):
                    add(messages)
                    raise memory.MemoryError('lost confirmation')
                result.add_messages = timeout
                return result
            client.session = session
            yield client
    monkeypatch.setattr(memory, 'honcho_client', uncertain)
    c.save('Question', 'Answer')
    assert local.pending(c.scope)[0]['state'] == 'uploading'
    monkeypatch.setattr(memory, 'honcho_client', remote)
    memory.sync_pending(c.scope)
    assert local.pending(c.scope) == []
    assert len(backend[0][(c.scope,c.id)]) == 2


def test_unconfirmed_missing_upload_is_not_blindly_retried(backend):
    create_project('Unknown')
    c = memory.open_conversation()
    local.append(c.scope,c.id,'Question','Answer')
    exchange = local.pending(c.scope)[0]
    local.set_state(exchange['id'], 'uploading')
    assert 'unconfirmed' in memory.sync_pending(c.scope)
    assert backend[0][(c.scope,c.id)] == []
    assert len(local.pending(c.scope)) == 1
    assert c.context('Next')[-1]['content'] == 'Answer'


def test_sync_is_project_scoped(backend, monkeypatch):
    create_project('A')
    a = memory.open_conversation()
    local.append(a.scope,a.id,'A','Answer A')
    create_project('B')
    b = memory.open_conversation()
    local.append(b.scope,b.id,'B','Answer B')
    memory.sync_pending(b.scope)
    assert len(local.pending(a.scope)) == 1
    assert local.pending(b.scope) == []
    assert (a.scope,a.id) not in backend[0]


def test_cached_remote_history_available_offline(backend, monkeypatch):
    create_project('Cached')
    c = memory.open_conversation()
    with memory.honcho_client(c.scope) as client:
        client.session(c.id).add_messages([{'role':'user','content':'From another machine'},{'role':'assistant','content':'Remote answer'}])
    assert len(c.context('Hello')) == 2
    monkeypatch.setattr(memory, 'honcho_client', offline)
    assert c.context('Next')[-1]['content'] == 'Remote answer'


def test_temporary_never_writes_sqlite(monkeypatch):
    c = memory.open_conversation(temporary=True)
    c.save('Question','Answer')
    assert c.context('Next')[-1]['content'] == 'Answer'
    assert not local.database_path().exists()


def test_database_error_is_readable(monkeypatch, tmp_path):
    path = tmp_path / 'bad.db'
    path.write_text('not sqlite')
    monkeypatch.setenv('SYNAPSE_MEMORY_DB',str(path))
    with pytest.raises(local.LocalMemoryError):
        local.remember_session('scope','session')


def test_second_sync_worker_does_not_upload(backend):
    create_project('Lock')
    c = memory.open_conversation()
    local.append(c.scope,c.id,'Question','Answer')
    with local.sync_lock() as acquired:
        assert acquired
        assert 'another synchronization' in memory.sync_pending(c.scope)
    assert backend[0] == {}


def test_remote_cache_does_not_overwrite_pending_history():
    create_project('Cache')
    c = memory.open_conversation()
    local.append(c.scope,c.id,'Offline question','Offline answer')
    local.cache_context(c.scope,c.id,[])
    assert local.context(c.scope,c.id)[-1]['content'] == 'Offline answer'
