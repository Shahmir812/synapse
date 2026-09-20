"""Exercise workspace selection, memory scope, and model requests together."""
import asyncio
from io import StringIO
import json
import sys
from unittest.mock import AsyncMock, Mock

import httpx
from openai import OpenAI
import pytest
from rich.console import Console

from synapse import ask, exploration, project_registry as registry
from synapse.local_memory import database_path
from tui import ask as ui, conversations


def prompts(monkeypatch, method, values):
    prompt = AsyncMock()
    prompt.ask_async.side_effect = values
    monkeypatch.setattr(ui.questionary, method, lambda *args, **kwargs: prompt)
    return prompt


def api(handler):
    return OpenAI(api_key='test', base_url='https://test.invalid/v1', max_retries=0,
                  http_client=httpx.Client(transport=httpx.MockTransport(handler)))


def completion(message):
    return {'id': 'test', 'created': 0, 'model': 'test-model', 'object': 'chat.completion',
            'choices': [{'index': 0, 'finish_reason': 'stop', 'message': message}]}


@pytest.mark.parametrize('excluded_only', [False, True])
def test_empty_explore_stops_before_memory_and_providers(tmp_path, monkeypatch, excluded_only):
    monkeypatch.chdir(tmp_path)
    if excluded_only:
        (tmp_path / '.env').write_text('PRIVATE=hidden')
        (tmp_path / '.git').mkdir()
        (tmp_path / '.git/config').write_text('hidden')
        (tmp_path / '.pytest_cache').mkdir()
        (tmp_path / '.pytest_cache/README.md').write_text('Generated cache metadata')
    memory = Mock()
    provider = Mock(side_effect=AssertionError('Provider must not be contacted'))
    monkeypatch.setattr(ask, 'create_model_client', provider)
    monkeypatch.setattr(ask, 'create_gemini_client', provider)
    with pytest.raises(ask.AskError, match='No explorable files') as error:
        ask.ask_question('What does this project do?', explore=True, conversation=memory)
    assert str(tmp_path) in str(error.value)
    assert 'PRIVATE' not in str(error.value)
    memory.context.assert_not_called()
    memory.save.assert_not_called()
    provider.assert_not_called()


def test_cli_empty_explore_does_not_open_remote_conversation(tmp_path, monkeypatch, capsys):
    import main
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(main, 'load_dotenv', lambda: None)
    monkeypatch.setattr(sys, 'argv', ['synapse', 'ask', 'Explain', '--explore', '--conversation', 'saved'])
    opener = Mock(side_effect=AssertionError('Must validate workspace first'))
    monkeypatch.setattr('synapse.memory.open_conversation', opener)
    with pytest.raises(SystemExit) as error:
        main.main()
    assert error.value.code == 1
    assert 'No explorable files' in capsys.readouterr().err
    opener.assert_not_called()


def test_incomplete_scan_is_not_claimed_empty(tmp_path, monkeypatch):
    monkeypatch.setattr(exploration.WorkspaceTools, 'files', lambda *args: iter([None]))
    assert exploration.inspect_workspace(tmp_path) == {'files': [], 'truncated': True}


def test_unreadable_scan_is_not_claimed_empty(tmp_path, monkeypatch):
    def denied(*args):
        raise PermissionError('Access denied')
    monkeypatch.setattr(exploration.WorkspaceTools, 'files', denied)
    with pytest.raises(exploration.ExplorationError, match='Cannot inspect workspace'):
        exploration.inspect_workspace(tmp_path)


def test_changed_workspace_blocks_even_temporary_history(tmp_path, monkeypatch):
    registry.register_project(tmp_path)
    other = tmp_path / 'other'
    other.mkdir()
    registry.register_project(other)
    memory = Mock(temporary=True)
    provider = Mock(side_effect=AssertionError('Wrong workspace must not be sent'))
    monkeypatch.setattr(ask, 'create_model_client', provider)
    with pytest.raises(ask.AskError, match='Active workspace changed'):
        ask.ask_question('Explain', conversation=memory, expected_workspace=tmp_path)
    provider.assert_not_called()
    memory.context.assert_not_called()


def test_tui_empty_workspace_back_never_opens_conversation_or_model(tmp_path, monkeypatch):
    empty = tmp_path / 'empty'
    empty.mkdir()
    registry.register_project(empty)
    prompts(monkeypatch, 'select', ['use', 'back'])
    opener = AsyncMock(side_effect=AssertionError('No memory before valid workspace'))
    monkeypatch.setattr(ui, 'choose_conversation', opener)
    monkeypatch.setattr(ui, 'ask_question', Mock(side_effect=AssertionError('No model for empty workspace')))
    output = StringIO()
    asyncio.run(ui.run_ask_mode(Console(file=output, width=240), explore=True))
    assert 'No explorable files' in output.getvalue()
    assert 'No request sent' in output.getvalue()
    opener.assert_not_awaited()


def test_pasted_workflow_switches_to_code_before_memory_and_fallback(tmp_path, monkeypatch):
    source = tmp_path / 'synapse-source'
    (source / 'synapse').mkdir(parents=True)
    (source / 'synapse/memory.py').write_text('def sync_pending():\n    """Upload queued SQLite exchanges to Honcho."""\n')
    registry.register_project(source)
    source_id = registry.current_project().id
    registry.create_project('new project')
    empty_root = registry.active_workspace()
    selections = iter(['use', 'switch', source_id, 'temporary'])
    prompt_order = []
    output = StringIO()

    def select(title, **kwargs):
        prompt_order.append(title)
        if title == 'Conversation':
            assert registry.active_workspace() == source
            assert 'synapse/memory.py' in output.getvalue()
        return Mock(ask_async=AsyncMock(return_value=next(selections)))

    monkeypatch.setattr(ui.questionary, 'select', select)
    monkeypatch.setattr(ui, 'choose_conversation', conversations.choose_conversation)
    question = 'Inspect synapse/memory.py. Explain SQLite synchronization to Honcho.'
    prompts(monkeypatch, 'text', [question, '/back'])
    prompts(monkeypatch, 'confirm', [False])
    sent = []

    def primary(request):
        sent.append(json.loads(request.content))
        return httpx.Response(402, json={'error': {'message': 'No credits'}})

    def fallback(request):
        body = json.loads(request.content)
        sent.append(body)
        if len(sent) == 2:
            return httpx.Response(200, json=completion({'role': 'assistant', 'content': None, 'tool_calls': [
                {'id': 'read-1', 'type': 'function', 'function': {'name': 'read_file',
                 'arguments': '{"path":"synapse/memory.py"}'}}]}))
        assert body['messages'][-1]['role'] == 'tool'
        assert 'Upload queued SQLite exchanges to Honcho' in body['messages'][-1]['content']
        return httpx.Response(200, json=completion({'role': 'assistant',
            'content': '**sync_pending** uploads queued SQLite exchanges to Honcho (`synapse/memory.py`).'}))

    monkeypatch.setattr(ask, 'create_model_client', lambda: (api(primary), 'primary'))
    monkeypatch.setattr(ask, 'create_gemini_client', lambda: (api(fallback), 'fallback'))
    monkeypatch.setattr(ask, 'gemini_is_configured', lambda: True)
    asyncio.run(ui.run_ask_mode(Console(file=output, width=240), explore=True))

    assert len(sent) == 3
    assert prompt_order == ['Workspace for this Ask session', 'Workspace for this Ask session', 'Choose a project', 'Conversation']
    assert all(str(source) in body['messages'][0]['content'] for body in sent)
    assert all(str(empty_root) not in json.dumps(body) for body in sent)
    assert 'No request sent' in output.getvalue()
    assert 'Falling back to Google AI Studio' in output.getvalue()
    assert 'synapse/memory.py: ' in output.getvalue()
    assert '**sync_pending**' not in output.getvalue()  # Markdown rendered by Rich.
    assert 'sync_pending uploads queued SQLite exchanges' in output.getvalue()
    assert 'local SQLite history ready' not in output.getvalue()
    assert not database_path().exists()


@pytest.mark.parametrize('mode', ['temporary', 'new'])
def test_tui_project_switch_opens_separate_history(tmp_path, monkeypatch, mode):
    first, second = tmp_path / 'a', tmp_path / 'b'
    first.mkdir()
    second.mkdir()
    (first / 'a.py').write_text('PROJECT_A')
    (second / 'b.py').write_text('PROJECT_B')
    registry.register_project(second)
    second_id = registry.current_project().id
    registry.register_project(first)
    prompts(monkeypatch, 'select', ['use', mode, 'switch', second_id, mode])
    prompts(monkeypatch, 'text', ['First question', '/project', 'Second question', '/back'])
    prompts(monkeypatch, 'confirm', [False, False])
    monkeypatch.setattr(ui, 'choose_conversation', conversations.choose_conversation)
    requests = []

    def answer(question, *, conversation, expected_workspace, **kwargs):
        requests.append((conversation.id, expected_workspace, conversation.context(question)))
        conversation.save(question, f'Answer about {expected_workspace.name}')
        return 'Answer'

    monkeypatch.setattr(ui, 'ask_question', answer)
    asyncio.run(ui.run_ask_mode(Console(file=StringIO()), explore=True))
    assert [request[1] for request in requests] == [first, second]
    assert [request[2] for request in requests] == [[], []]
    assert requests[0][0] != requests[1][0]


def test_back_after_switching_to_empty_workspace_leaves_ask(tmp_path, monkeypatch):
    first, empty = tmp_path / 'code', tmp_path / 'empty'
    first.mkdir()
    empty.mkdir()
    (first / 'app.py').write_text('CODE')
    registry.register_project(empty)
    empty_id = registry.current_project().id
    registry.register_project(first)
    prompts(monkeypatch, 'select', ['use', 'switch', empty_id, 'back'])
    text = prompts(monkeypatch, 'text', ['/project'])
    model = Mock(side_effect=AssertionError('Must leave the old conversation'))
    monkeypatch.setattr(ui, 'ask_question', model)
    asyncio.run(ui.run_ask_mode(Console(file=StringIO()), explore=True))
    assert registry.active_workspace() == empty
    assert text.ask_async.await_count == 1
    model.assert_not_called()


@pytest.mark.parametrize('result,summary', [
    ({'files': [], 'truncated': False}, '0 file(s) found'),
    ({'matches': [{}], 'truncated': True}, '1 matching line(s) found · truncated'),
    ({'path': 'app.py', 'content': 'CODE', 'truncated': False}, 'app.py: 4 characters read'),
    ({'error': 'File missing'}, 'error: File missing'),
])
def test_tool_status_describes_results(result, summary):
    assert exploration.describe_result(json.dumps(result)) == summary
