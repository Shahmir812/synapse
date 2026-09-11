from contextlib import contextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock
import asyncio
from io import StringIO

import pytest
from rich.console import Console

from synapse import memory, ask
from synapse.project_registry import create_project, rename_project


@pytest.fixture
def backend(monkeypatch):
    saved = {}
    calls = []
    class Peer:
        def __init__(self, id): self.id = id
        def message(self, content, **kwargs): return {'metadata': kwargs.get('metadata', {}), 'role': 'assistant' if self.id == 'synapse' else 'user', 'content': content}
    class Session:
        def __init__(self, scope, id): self.scope, self.id = scope, id
        def context(self, **kwargs):
            calls.append(('context', kwargs))
            return SimpleNamespace(to_openai=lambda **kwargs: list(saved[(self.scope, self.id)]))
        def messages(self, **kwargs):
            exchange_id = kwargs['filters']['metadata']['synapse_exchange_id']
            return SimpleNamespace(items=[SimpleNamespace(peer_id='synapse' if m['role']=='assistant' else 'user', content=m['content'])
                for m in saved[(self.scope,self.id)] if m.get('metadata', {}).get('synapse_exchange_id') == exchange_id])
        def add_messages(self, messages):
            calls.append(('save', messages))
            saved[(self.scope, self.id)].extend(messages)
    class Client:
        def __init__(self, scope): self.scope = scope
        def session(self, id, **kwargs):
            saved.setdefault((self.scope, id), [])
            return Session(self.scope, id)
        def peer(self, id): return Peer(id)
        def sessions(self, **kwargs):
            selected = kwargs.get('filters', {}).get('metadata', {}).get('synapse_session_id')
            items = [SimpleNamespace(id=id) for scope, id in saved if scope == self.scope and (not selected or id == selected)]
            return SimpleNamespace(items=items, has_next_page=lambda: False)
    @contextmanager
    def client(scope): yield Client(scope)
    monkeypatch.setattr(memory, 'honcho_client', client)
    return saved, calls


def test_new_resume_project_isolation_and_rename(backend):
    create_project('First')
    c = memory.open_conversation()
    c.save('Prefer small commits', 'Understood')
    rename_project('Renamed')
    resumed = memory.open_conversation(c.id)
    assert len(resumed.context('What did I prefer?')) == 2
    assert memory.conversations()[0] == [c.id]
    create_project('Second')
    assert memory.conversations()[0] == []
    with pytest.raises(memory.MemoryError, match='not found'):
        memory.open_conversation(c.id)
    with pytest.raises(memory.MemoryError, match='changed'):
        c.context('Hello')


def test_fallback_same_history_and_one_save_without_raw_files(backend, monkeypatch):
    create_project('First')
    c = memory.open_conversation()
    c.save('Earlier question', 'Earlier answer')
    from synapse.project_registry import active_workspace
    (active_workspace() / 'code.py').write_text('RAW_FILE_CONTENT')
    requests = []
    def request(question, report, provider, *args):
        requests.append((question, args[-1]))
        if provider == 'OpenRouter': raise ask.AskError('failure')
        return 'Successful answer'
    monkeypatch.setattr(ask, '_request', request)
    monkeypatch.setattr(ask, 'gemini_is_configured', lambda: True)
    assert ask.ask_question('Explain', files=['code.py'], conversation=c) == 'Successful answer'
    assert requests[0] == requests[1]
    assert 'RAW_FILE_CONTENT' in requests[0][0]
    saved, calls = backend
    assert len(saved[(c.scope, c.id)]) == 4
    assert 'RAW_FILE_CONTENT' not in str(saved)
    context_call = next(v for k,v in calls if k == 'context')
    assert context_call['tokens'] == 3000 and context_call['peer_target'] == 'user'


def test_failed_generation_not_saved(backend, monkeypatch):
    create_project('First')
    c = memory.open_conversation()
    def fail(*args): raise ask.AskError('failed')
    monkeypatch.setattr(ask, '_request', fail)
    monkeypatch.setattr(ask, 'gemini_is_configured', lambda: False)
    with pytest.raises(ask.AskError): ask.ask_question('Hello', conversation=c)
    assert backend[0].get((c.scope, c.id), []) == []


def test_read_failure_stops_before_provider(backend, monkeypatch):
    c = memory.open_conversation(temporary=True)
    def fail(*args): raise memory.MemoryError('offline')
    monkeypatch.setattr(c, 'context', fail)
    monkeypatch.setattr(ask, '_request', lambda *args: pytest.fail('Provider called'))
    with pytest.raises(memory.MemoryError): ask.ask_question('Hello', conversation=c)


def test_save_failure_keeps_answer_and_reports_warning(backend, monkeypatch):
    c = memory.open_conversation(temporary=True)
    def fail(*args): raise memory.MemoryError('offline')
    monkeypatch.setattr(c, 'save', fail)
    monkeypatch.setattr(ask, '_request', lambda *args: 'Answer')
    status = []
    assert ask.ask_question('Hello', conversation=c, on_status=status.append) == 'Answer'
    assert any('not confirmed' in line for line in status)


def test_temporary_bounded_and_no_honcho(monkeypatch):
    monkeypatch.setattr(memory, 'honcho_client', lambda *args: pytest.fail('Network called'))
    c = memory.open_conversation(temporary=True)
    for i in range(20): c.save('q' * 1000, 'a' * 1000)
    assert sum(len(m['content']) for m in c.context('Hello')) <= memory.MAX_CONTEXT_CHARS
    assert memory.open_conversation(temporary=True).history == []


def test_unknown_resume_does_not_create_session(backend):
    create_project('First')
    with pytest.raises(memory.MemoryError): memory.open_conversation('missing')
    assert backend[0] == {}


def test_missing_key_and_no_project(monkeypatch):
    with pytest.raises(memory.MemoryError, match='Select a project'):
        memory.open_conversation()
    create_project('First')
    assert not memory.open_conversation().temporary


def test_service_error_is_sanitized(monkeypatch):
    monkeypatch.setenv('HONCHO_API_KEY', 'secret')
    def fail(**kwargs): raise RuntimeError('secret request data')
    monkeypatch.setattr(memory, 'Honcho', fail)
    with pytest.raises(memory.MemoryError) as error:
        with memory.honcho_client('scope'): pass
    assert 'secret' not in str(error.value)


def test_offline_save_continues_locally(backend, monkeypatch):
    create_project('First')
    c = memory.open_conversation()
    @contextmanager
    def fail(scope): raise memory.MemoryError('offline'); yield
    monkeypatch.setattr(memory, 'honcho_client', fail)
    c.save('Hello', 'Answer')
    assert 'saved locally' in c.status
    assert c.context('Next') == [{'role':'user','content':'Hello'},{'role':'assistant','content':'Answer'}]


def test_tui_outage_can_choose_temporary(monkeypatch):
    from tui import conversations
    create_project('TUI Project')
    monkeypatch.setattr(conversations, 'configured', lambda: True)
    selection = AsyncMock()
    selection.ask_async.side_effect = ['new', 'temporary']
    monkeypatch.setattr(conversations.questionary, 'select', lambda *args, **kwargs: selection)
    original = memory.open_conversation
    def open_session(*args, **kwargs):
        if not kwargs.get('temporary'): raise memory.MemoryError('offline')
        return original(temporary=True)
    monkeypatch.setattr(conversations, 'open_conversation', open_session)
    output = StringIO()
    result = asyncio.run(conversations.choose_conversation(Console(file=output)))
    assert result.temporary and 'offline' in output.getvalue()


def test_tui_resume_selection(backend, monkeypatch):
    from tui import conversations
    create_project('First')
    c = memory.open_conversation()
    c.save('Hello', 'Answer')
    monkeypatch.setattr(conversations, 'configured', lambda: True)
    selection = AsyncMock()
    selection.ask_async.side_effect = ['resume', c.id]
    monkeypatch.setattr(conversations.questionary, 'select', lambda *args, **kwargs: selection)
    result = asyncio.run(conversations.choose_conversation(Console(file=StringIO())))
    assert result.id == c.id and result.scope == c.scope


def test_cli_resume_and_temporary(backend, monkeypatch):
    import main
    import sys
    create_project('First')
    c = memory.open_conversation()
    monkeypatch.setenv('HONCHO_API_KEY', 'test')
    monkeypatch.setattr(main, 'load_dotenv', lambda: None)
    received = []
    def answer(question, **kwargs):
        received.append(kwargs['conversation'])
        return 'Answer'
    monkeypatch.setattr(ask, 'ask_question', answer)
    monkeypatch.setattr(sys, 'argv', ['synapse', 'ask', 'Hello', '--conversation', c.id])
    main.main()
    assert received[-1].id == c.id
    monkeypatch.setattr(sys, 'argv', ['synapse', 'ask', 'Hello', '--temporary'])
    main.main()
    assert received[-1] is None
