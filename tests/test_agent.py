import asyncio
from io import StringIO
import json
import os
from pathlib import Path
import sys
from threading import Event, Timer
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import httpx
from openai import OpenAI
import pytest
from rich.console import Console

from synapse import agent, ask, project_registry as registry
from synapse.memory import open_conversation
from tui import agent as ui


def make_tools(root, approve=lambda proposal: True, **kwargs):
    return agent.AgentTools(root, approve, agent.RunRecord(root), **kwargs)


def invoke(tools, name, **args):
    return json.loads(tools.execute(name, json.dumps(args)))


def test_create_edit_delete_review_and_record(tmp_path):
    proposals = []
    tools = make_tools(tmp_path, lambda p: proposals.append(p) or True)
    assert invoke(tools, 'create_file', path='src/app.py', content='answer = 1\n', reason='Create')['status'].startswith('create_file applied')
    path = tmp_path / 'src/app.py'
    path.chmod(0o755)
    invoke(tools, 'read_file', path='src/app.py')
    invoke(tools, 'edit_file', path='src/app.py', old_text='1', new_text='2', reason='Fix answer')
    assert path.read_text() == 'answer = 2\n'
    assert path.stat().st_mode & 0o777 == 0o755
    assert '-answer = 1\n+answer = 2' in proposals[1].diff
    invoke(tools, 'delete_file', path='src/app.py', reason='Remove')
    assert not path.exists()
    record = json.loads(tools.record.path.read_text())
    assert [a['status'] for a in record['actions']] == ['applied'] * 3
    assert all(p.workspace == str(tmp_path) for p in proposals)


@pytest.mark.parametrize('kind', ['create_file', 'edit_file', 'delete_file'])
def test_denied_changes_leave_files_unchanged(tmp_path, kind):
    tools = make_tools(tmp_path, lambda p: False)
    path = tmp_path / 'app.py'
    args = dict(path='app.py', reason='Test')
    if kind == 'create_file':
        args['content'] = 'new'
    else:
        path.write_text('old')
        invoke(tools, 'read_file', path='app.py')
        if kind == 'edit_file':
            args.update(old_text='old', new_text='new')
    assert 'Denied' in invoke(tools, kind, **args)['status']
    assert not path.exists() if kind == 'create_file' else path.read_text() == 'old'
    assert tools.record.data['actions'][-1]['status'] == 'denied'


@pytest.mark.parametrize('path', ['../escape', '/tmp/escape', '.env', '.git/config', '.synapse/run.json', 'link/file', 'bad\x1bname'])
def test_mutating_paths_blocked_before_review(tmp_path, path):
    (tmp_path / 'link').symlink_to(tmp_path.parent, target_is_directory=True)
    approve = Mock(return_value=True)
    tools = make_tools(tmp_path, approve)
    assert 'error' in invoke(tools, 'create_file', path=path, content='bad', reason='Test')
    approve.assert_not_called()


def test_create_never_overwrites_and_unread_edit_rejected(tmp_path):
    (tmp_path / 'a.py').write_text('original')
    tools = make_tools(tmp_path)
    assert 'error' in invoke(tools, 'create_file', path='a.py', content='new', reason='Test')
    assert 'error' in invoke(tools, 'edit_file', path='a.py', old_text='original', new_text='new', reason='Test')
    assert (tmp_path / 'a.py').read_text() == 'original'


@pytest.mark.parametrize('when', ['after_read', 'during_approval'])
def test_stale_edit_does_not_clobber_user_work(tmp_path, when):
    path = tmp_path / 'a.py'
    path.write_text('old')
    def approve(p):
        if when == 'during_approval':
            path.write_text('user edit')
        return True
    tools = make_tools(tmp_path, approve)
    invoke(tools, 'read_file', path='a.py')
    if when == 'after_read':
        path.write_text('user edit')
    assert 'error' in invoke(tools, 'edit_file', path='a.py', old_text='old', new_text='new', reason='Test')
    assert path.read_text() == 'user edit'


def test_create_race_and_symlink_swap_do_not_write(tmp_path):
    target = tmp_path / 'file.py'
    def approve(p):
        target.write_text('user file')
        return True
    tools = make_tools(tmp_path, approve)
    assert 'error' in invoke(tools, 'create_file', path='file.py', content='agent file', reason='Test')
    assert target.read_text() == 'user file'
    target.unlink()
    def swap(p):
        target.symlink_to(tmp_path.parent / 'outside')
        return True
    tools.approve = swap
    assert 'error' in invoke(tools, 'create_file', path='file.py', content='agent file', reason='Test')


def test_read_pagination_and_exact_replacement(tmp_path):
    path = tmp_path / 'large.py'
    path.write_text(''.join(f'line{i}\n' for i in range(300)))
    tools = make_tools(tmp_path)
    first = invoke(tools, 'read_file', path='large.py')
    assert first['next_line'] == 201 and first['truncated']
    second = invoke(tools, 'read_file', path='large.py', start_line=201)
    assert '300: line299' in second['content'] and not second['truncated']
    assert 'error' in invoke(tools, 'edit_file', path='large.py', old_text='line', new_text='x', reason='Ambiguous')
    assert 'error' in invoke(tools, 'create_file', path='large.txt', content='x' * 65537, reason='Too large')


@pytest.mark.parametrize('name,args', [('bogus', {}), ('edit_file', {}), ('read_file', {'path': 'a', 'extra': 1}),
                                      ('update_plan', {'steps': []}), ('run_command', {'argv': 'ls', 'reason': 'test'})])
def test_invalid_tools(tmp_path, name, args):
    assert 'error' in invoke(make_tools(tmp_path), name, **args)


@pytest.mark.parametrize('start,end', [('bad', 2), (True, 2), (0, 2), (1, 'bad'), (1, 500)])
def test_bad_read_ranges_return_tool_errors(tmp_path, start, end):
    (tmp_path / 'a.py').write_text('content')
    assert 'error' in invoke(make_tools(tmp_path), 'read_file', path='a.py', start_line=start, end_line=end)


def test_plan_and_command_results(tmp_path, monkeypatch):
    monkeypatch.setenv('OPENROUTER_API_KEY', 'must-not-inherit')
    statuses = []
    tools = make_tools(tmp_path, report=statuses.append)
    invoke(tools, 'update_plan', steps=[{'step': 'Test code', 'status': 'in_progress'}])
    assert 'Test code' in statuses[-1]
    script = 'import os,sys; print(os.getcwd()); print(sys.argv[1]); assert "OPENROUTER_API_KEY" not in os.environ'
    result = invoke(tools, 'run_command', argv=[sys.executable, '-c', script, '*.py'], reason='Check environment')
    assert result['status'] == 'completed' and result['exit_code'] == 0
    assert str(tmp_path) in result['output'] and '*.py' in result['output']
    failed = invoke(tools, 'run_command', argv=[sys.executable, '-c', 'raise SystemExit(7)'], reason='Failure')
    assert failed['status'] == 'failed' and failed['exit_code'] == 7


def test_denied_command_does_not_start(tmp_path, monkeypatch):
    start = Mock(side_effect=AssertionError('must not execute'))
    monkeypatch.setattr(agent.subprocess, 'Popen', start)
    result = invoke(make_tools(tmp_path, lambda p: False), 'run_command', argv=['echo', 'hi'], reason='Test')
    assert 'Denied' in result['status']
    start.assert_not_called()


def test_command_timeout_and_output_limit(tmp_path):
    tools = make_tools(tmp_path)
    result = invoke(tools, 'run_command', argv=[sys.executable, '-c', 'import time; time.sleep(10)'], timeout=1, reason='Timeout')
    assert result['status'] == 'timed_out'
    result = invoke(tools, 'run_command', argv=[sys.executable, '-c', 'while True: print("x" * 10000)'], reason='Output cap')
    assert result['status'] == 'output_limit' and result['truncated']
    assert len(result['output']) <= 8000


def test_command_cancellation_kills_child(tmp_path):
    event = Event()
    tools = make_tools(tmp_path, cancel_event=event)
    timer = Timer(0.3, event.set)
    timer.start()
    try:
        with pytest.raises(agent.AgentError, match='cancelled'):
            invoke(tools, 'run_command', argv=[sys.executable, '-c', 'import time; time.sleep(20)'], reason='Test')
    finally:
        timer.cancel()
    assert tools.record.data['actions'][-1]['status'] == 'interrupted_or_failed'


def test_cancel_during_approval_prevents_write(tmp_path):
    event = Event()
    tools = make_tools(tmp_path, lambda p: event.set() or True, cancel_event=event)
    with pytest.raises(agent.AgentError):
        invoke(tools, 'create_file', path='app.py', content='code', reason='Test')
    assert not (tmp_path / 'app.py').exists()


def model_client(handler):
    return OpenAI(api_key='mock', base_url='https://mock.invalid/v1', max_retries=0,
                  http_client=httpx.Client(transport=httpx.MockTransport(handler)))


def response(name=None, args=None, identifier='id', content='Done'):
    message = {'role': 'assistant', 'content': content if name is None else None}
    if name:
        message['tool_calls'] = [{'id': identifier, 'type': 'function', 'function': {'name': name, 'arguments': json.dumps(args)}}]
    return httpx.Response(200, json={'id': 'mock', 'created': 0, 'model': 'mock', 'object': 'chat.completion',
                                    'choices': [{'index': 0, 'finish_reason': 'tool_calls' if name else 'stop', 'message': message}]})


def test_complete_agent_build_test_fix_cycle(tmp_path, monkeypatch):
    registry.register_project(tmp_path)
    actions = [
        ('update_plan', {'steps': [{'step': 'Implement and test answer()', 'status': 'in_progress'}]}),
        ('create_file', {'path': 'app.py', 'content': 'def answer():\n    return 1\n', 'reason': 'Implement'}),
        ('create_file', {'path': 'test_app.py', 'content': 'from app import answer\ndef test_answer():\n    assert answer() == 2\n', 'reason': 'Test'}),
        ('run_command', {'argv': [sys.executable, '-B', '-m', 'pytest', '-q'], 'reason': 'Run tests'}),
        ('read_file', {'path': 'app.py'}),
        ('edit_file', {'path': 'app.py', 'old_text': 'return 1', 'new_text': 'return 2', 'reason': 'Fix failed test'}),
        ('run_command', {'argv': [sys.executable, '-B', '-m', 'pytest', '-q'], 'reason': 'Verify fix'}),
        ('update_plan', {'steps': [{'step': 'Implement and test answer()', 'status': 'completed'}]}),
    ]
    count = 0
    def respond(request):
        nonlocal count
        body = json.loads(request.content)
        assert any(t['function']['name'] == 'edit_file' for t in body['tools'])
        if count == 4:
            assert json.loads(body['messages'][-1]['content'])['exit_code'] != 0
        if count == 7:
            result = json.loads(body['messages'][-1]['content'])
            assert result['exit_code'] == 0 and '1 passed' in result['output']
        if count >= len(actions):
            return response(content='Implemented answer() and verified the test passes.')
        name, args = actions[count]
        count += 1
        return response(name, args, f'call-{count}')
    approvals = []
    monkeypatch.setattr(ask, 'create_model_client', lambda: (model_client(respond), 'mock'))
    conversation = open_conversation(temporary=True)
    answer = ask.ask_question('Build answer() returning 2 with a test', agent=True,
                              approve=lambda p: approvals.append(p) or True, conversation=conversation)
    assert (tmp_path / 'app.py').read_text().endswith('return 2\n')
    assert len(approvals) == 5
    assert 'finished' in answer and 'exit 0' in answer
    assert conversation.history[-1]['content'] == answer
    records = list((tmp_path / '.synapse/agent-runs').glob('*.json'))
    assert len(records) == 1
    record = json.loads(records[0].read_text())
    assert record['status'] == 'finished'
    assert record['plan'][0]['status'] == 'completed'


def test_fallback_keeps_applied_edit_and_record(tmp_path, monkeypatch):
    registry.register_project(tmp_path)
    calls = 0
    def primary(request):
        nonlocal calls
        calls += 1
        if calls == 1:
            return response('create_file', {'path': 'a.py', 'content': 'CODE', 'reason': 'Create'}, 'write-1')
        return httpx.Response(503, json={'error': {'message': 'offline'}})
    def fallback(request):
        body = json.loads(request.content)
        assert 'create_file applied' in body['messages'][-1]['content']
        return response()
    approve = Mock(return_value=True)
    monkeypatch.setattr(ask, 'create_model_client', lambda: (model_client(primary), 'primary'))
    monkeypatch.setattr(ask, 'create_gemini_client', lambda: (model_client(fallback), 'fallback'))
    monkeypatch.setattr(ask, 'gemini_is_configured', lambda: True)
    ask.ask_question('Create a.py', agent=True, approve=approve)
    approve.assert_called_once()
    assert (tmp_path / 'a.py').read_text() == 'CODE'


def test_failure_reports_changes_already_applied(tmp_path, monkeypatch):
    registry.register_project(tmp_path)
    count = 0
    def respond(request):
        nonlocal count
        count += 1
        if count == 1:
            return response('create_file', {'path': 'keep.py', 'content': 'KEEP', 'reason': 'Create'})
        return httpx.Response(401, json={'error': {'message': 'bad auth'}})
    monkeypatch.setattr(ask, 'create_model_client', lambda: (model_client(respond), 'mock'))
    monkeypatch.setattr(ask, 'gemini_is_configured', lambda: False)
    reports = []
    with pytest.raises(ask.AskError, match='authentication'):
        ask.ask_question('Create file', agent=True, approve=lambda p: True, on_status=reports.append)
    assert (tmp_path / 'keep.py').read_text() == 'KEEP'
    assert 'failed' in reports[-1] and 'applied' in reports[-1]


def test_duplicate_call_id_is_not_replayed(tmp_path):
    run = agent.Agent(tmp_path, 'Test', [], approve=lambda p: True)
    call = SimpleNamespace(id='one', function=SimpleNamespace(name='create_file', arguments=json.dumps(
        {'path': 'a.py', 'content': 'A', 'reason': 'Test'})))
    assert run.execute_call(call) == run.execute_call(call)
    assert len(run.record.data['actions']) == 1
    call.function.arguments = '{}'
    with pytest.raises(agent.AgentError, match='reused'):
        run.execute_call(call)


def test_scope_change_during_approval_stops_action(tmp_path, monkeypatch):
    first, other = tmp_path / 'first', tmp_path / 'other'
    first.mkdir()
    other.mkdir()
    registry.register_project(first)
    def approve(p):
        registry.register_project(other)
        return True
    monkeypatch.setattr(ask, 'create_model_client', lambda: (model_client(lambda r: response('create_file',
        {'path': 'a.py', 'content': 'A', 'reason': 'Test'})), 'mock'))
    with pytest.raises(ask.AskError, match='Active project changed'):
        ask.ask_question('Create a.py', agent=True, approve=approve)
    assert not (first / 'a.py').exists() and not (other / 'a.py').exists()


def test_cli_noninteractive_review_denies(tmp_path, monkeypatch):
    monkeypatch.setattr(sys, 'stdin', StringIO())
    assert ui.cli_approval(Console(file=StringIO()))(agent.Proposal('create_file', str(tmp_path), 'Test')) is False


@pytest.mark.parametrize('choice,approved', [('approve', True), ('reject', False)])
def test_tui_approval_bridge(tmp_path, monkeypatch, choice, approved):
    prompt = Mock(ask_async=AsyncMock(return_value=choice))
    monkeypatch.setattr(ui.questionary, 'select', lambda *args, **kwargs: prompt)
    def task(question, **kwargs):
        assert kwargs['agent'] is True
        result = kwargs['approve'](agent.Proposal('create_file', str(tmp_path), 'Test', 'a.py', '+A'))
        assert result is approved
        return 'Done'
    monkeypatch.setattr(ui, 'ask_question', task)
    result = asyncio.run(ui.run_task(Console(file=StringIO()), 'Task', tmp_path, None))
    assert result == 'Done'


def test_tui_stop_sets_cancellation(tmp_path, monkeypatch):
    prompt = Mock(ask_async=AsyncMock(return_value='stop'))
    monkeypatch.setattr(ui.questionary, 'select', lambda *args, **kwargs: prompt)
    def task(question, **kwargs):
        assert not kwargs['approve'](agent.Proposal('create_file', str(tmp_path), 'Test'))
        assert kwargs['cancel_event'].is_set()
        return 'Stopped'
    monkeypatch.setattr(ui, 'ask_question', task)
    asyncio.run(ui.run_task(Console(file=StringIO()), 'Task', tmp_path, None))


def test_launcher_routes_agent(monkeypatch):
    from tui import wakeup
    prompt = Mock(ask_async=AsyncMock(side_effect=['agent', 'exit']))
    monkeypatch.setattr(wakeup.questionary, 'select', lambda *args, **kwargs: prompt)
    monkeypatch.setattr(wakeup, 'Console', lambda: Console(file=StringIO(), force_terminal=True))
    monkeypatch.setattr(wakeup, 'show_banner', AsyncMock())
    monkeypatch.setattr(wakeup, 'import_current_project', lambda: None)
    runner = AsyncMock()
    monkeypatch.setattr(ui, 'run_agent_mode', runner)
    asyncio.run(wakeup.run_wakeup())
    runner.assert_awaited_once()


def test_cli_agent_routes_task(monkeypatch):
    import main
    monkeypatch.setattr(main, 'load_dotenv', lambda: None)
    monkeypatch.setattr(sys, 'argv', ['synapse', 'agent', 'Build hello', '--temporary'])
    runner = Mock(return_value='Done')
    monkeypatch.setattr(ask, 'ask_question', runner)
    main.main()
    assert runner.call_args.args == ('Build hello',)
    assert runner.call_args.kwargs['agent'] is True
    assert callable(runner.call_args.kwargs['approve'])


def test_run_records_reject_symlink_directory(tmp_path):
    elsewhere = tmp_path / 'elsewhere'
    elsewhere.mkdir()
    (tmp_path / '.synapse').symlink_to(elsewhere, target_is_directory=True)
    with pytest.raises(agent.AgentError, match='symlinks'):
        agent.RunRecord(tmp_path)
    assert not list(elsewhere.iterdir())


def test_agent_round_limit_preserves_applied_changes(tmp_path, monkeypatch):
    registry.register_project(tmp_path)
    monkeypatch.setattr(agent.Agent, 'max_rounds', 1)
    monkeypatch.setattr(ask, 'create_model_client', lambda: (model_client(lambda r: response('create_file',
        {'path': 'keep.py', 'content': 'KEEP', 'reason': 'Create'})), 'mock'))
    monkeypatch.setattr(ask, 'gemini_is_configured', lambda: True)
    fallback = Mock(side_effect=AssertionError('A tool limit must not trigger fallback'))
    monkeypatch.setattr(ask, 'create_gemini_client', fallback)
    with pytest.raises(ask.AskError, match='round limit'):
        ask.ask_question('Task', agent=True, approve=lambda p: True)
    assert (tmp_path / 'keep.py').read_text() == 'KEEP'
    fallback.assert_not_called()


def test_journal_failure_prevents_mutation(tmp_path, monkeypatch):
    tools = make_tools(tmp_path)
    def failure():
        raise agent.AgentError('Cannot save run record')
    monkeypatch.setattr(tools.record, 'save', failure)
    with pytest.raises(agent.AgentError):
        invoke(tools, 'create_file', path='a.py', content='A', reason='Test')
    assert not (tmp_path / 'a.py').exists()


def test_tui_agent_empty_project_and_followup(monkeypatch, tmp_path):
    registry.register_project(tmp_path)
    monkeypatch.setattr(ui, 'choose_workspace', AsyncMock(return_value=True))
    conversation = open_conversation(temporary=True)
    monkeypatch.setattr(ui, 'choose_conversation', AsyncMock(return_value=conversation))
    prompt = Mock(ask_async=AsyncMock(side_effect=['Build hello', 'Add tests', '/back']))
    monkeypatch.setattr(ui.questionary, 'text', lambda *args, **kwargs: prompt)
    runner = AsyncMock(return_value='**Done**')
    monkeypatch.setattr(ui, 'run_task', runner)
    output = StringIO()
    asyncio.run(ui.run_agent_mode(Console(file=output)))
    assert [call.args[1] for call in runner.await_args_list] == ['Build hello', 'Add tests']
    assert all(call.args[2:] == (tmp_path, conversation) for call in runner.await_args_list)
    assert '**Done**' not in output.getvalue()


def test_command_invalidates_read_snapshot(tmp_path):
    tools = make_tools(tmp_path)
    (tmp_path / 'a.py').write_text('old')
    invoke(tools, 'read_file', path='a.py')
    invoke(tools, 'run_command', argv=[sys.executable, '-c', 'print("ok")'], reason='Test')
    assert 'error' in invoke(tools, 'edit_file', path='a.py', old_text='old', new_text='new', reason='Must reread')
