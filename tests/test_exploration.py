from io import StringIO
from unittest.mock import AsyncMock
import asyncio
import json

import httpx
from openai import OpenAI
import pytest

from synapse import ask, exploration
from synapse.exploration import WorkspaceTools, Exploration, ExplorationError
from tui import wakeup


def completion(content=None, calls=None):
    message = {'role':'assistant', 'content':content}
    if calls is not None: message['tool_calls'] = calls
    return {'id':'test', 'created':0, 'model':'test-model', 'object':'chat.completion',
            'choices':[{'index':0,'finish_reason':'tool_calls' if calls else 'stop','message':message}]}


def call(name, args, id='call-1'):
    return {'id':id,'type':'function','function':{'name':name,'arguments':args}}


def client(handler):
    return OpenAI(api_key='test', base_url='https://test.invalid/v1', max_retries=0,
                  http_client=httpx.Client(transport=httpx.MockTransport(handler)))


def test_tools_read_list_search(tmp_path):
    (tmp_path / 'a.py').write_text('def hello():\n    return 42\n')
    (tmp_path / '.env').write_text('SECRET=hidden')
    (tmp_path / '.git').mkdir()
    (tmp_path / '.git' / 'config').write_text('hidden')
    tools = WorkspaceTools(tmp_path)
    listed = json.loads(tools.execute('list_files','{}'))
    assert listed['files'] == ['a.py']
    assert 'return 42' in tools.execute('read_file',json.dumps({'path':'a.py'}))
    matches = json.loads(tools.execute('search_files',json.dumps({'query':'42','pattern':'*.py'})))
    assert matches['matches'] == [{'path':'a.py','line':2,'text':'    return 42'}]
    assert not json.loads(tools.execute('search_files',json.dumps({'query':'SECRET'})))['matches']


@pytest.mark.parametrize('path', ['../outside','.env','/etc/passwd','.git/config','missing','link'])
def test_blocked_paths(tmp_path, path):
    (tmp_path / '.env').write_text('secret')
    (tmp_path / 'link').symlink_to(tmp_path / '.env')
    result = json.loads(WorkspaceTools(tmp_path).execute('read_file',json.dumps({'path':path})))
    assert 'error' in result and 'secret' not in str(result)


@pytest.mark.parametrize('name,args', [('shell','{}'),('read_file','[]'),('read_file','{'),('read_file','{}'),('list_files','{"extra":1}'),('search_files','{"query":42}')])
def test_invalid_tools_and_arguments(tmp_path,name,args):
    assert 'error' in json.loads(WorkspaceTools(tmp_path).execute(name,args))


def test_result_limits(tmp_path):
    (tmp_path / 'large.txt').write_text('x' * 20000)
    result = WorkspaceTools(tmp_path).execute('read_file','{"path":"large.txt"}')
    assert len(result) <= exploration.MAX_OUTPUT
    assert json.loads(result)['truncated']
    for i in range(201): (tmp_path / f'{i}.txt').write_text('match\n')
    assert json.loads(WorkspaceTools(tmp_path).execute('list_files','{}'))['truncated']
    result = json.loads(WorkspaceTools(tmp_path).execute('search_files','{"query":"match"}'))
    assert result['truncated'] and len(result['matches']) <= 50


def test_loop_tool_sequence_and_final_answer(tmp_path):
    (tmp_path / 'a.py').write_text('ANSWER = 42')
    requests = []
    def respond(request):
        body = json.loads(request.content)
        requests.append(body)
        if len(requests) == 1:
            assert body["tool_choice"] == "required"
            return httpx.Response(200,json=completion(calls=[call('read_file','{"path":"a.py"}')]))
        assert body['tool_choice'] == 'auto'
        assert body['messages'][-1]['role'] == 'tool'
        assert body['messages'][-1]['tool_call_id'] == 'call-1'
        assert 'ANSWER = 42' in body['messages'][-1]['content']
        return httpx.Response(200,json=completion('The answer is 42 in a.py.'))
    run = Exploration(tmp_path,'Explain',[])
    status=[]
    with client(respond) as api:
        result = run.run(api,'test-model',status.append)
    assert result.choices[0].message.content.startswith('The answer')
    assert run.calls == 1 and len(requests) == 2
    assert any('read_file' in item for item in status)


def test_unknown_tool_error_returned_to_model(tmp_path):
    count=0
    def respond(request):
        nonlocal count
        count+=1
        if count==1: return httpx.Response(200,json=completion(calls=[call('shell','{"cmd":"rm -rf ."}')]))
        assert 'error' in json.loads(request.content)['messages'][-1]['content']
        return httpx.Response(200,json=completion('Cannot run commands.'))
    with client(respond) as api:
        Exploration(tmp_path,'Hello',[]).run(api,'test',lambda _:None)


def test_round_limit(tmp_path):
    def respond(request): return httpx.Response(200,json=completion(calls=[call('list_files','{}')]))
    run = Exploration(tmp_path,'Hello',[])
    with client(respond) as api, pytest.raises(ExplorationError,match='round limit'):
        run.run(api,'test',lambda _:None)
    assert run.rounds == exploration.MAX_ROUNDS


def test_batch_call_limit_executes_nothing(tmp_path):
    def respond(request): return httpx.Response(200,json=completion(calls=[call('list_files','{}',f'call-{i}') for i in range(13)]))
    run = Exploration(tmp_path,'Hello',[])
    with client(respond) as api, pytest.raises(ExplorationError,match='tool-call limit'):
        run.run(api,'test',lambda _:None)
    assert run.calls == 0


def test_fallback_preserves_completed_tool_results(tmp_path,monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / 'a.py').write_text('ORIGINAL')
    count=0
    def primary(request):
        nonlocal count
        count+=1
        if count==1: return httpx.Response(200,json=completion(calls=[call('read_file','{"path":"a.py"}')]))
        (tmp_path / 'a.py').write_text('CHANGED')
        return httpx.Response(503,json={'error':{'message':'offline'}})
    def fallback(request):
        messages=json.loads(request.content)['messages']
        assert messages[-1]['role']=='tool'
        assert 'ORIGINAL' in messages[-1]['content'] and 'CHANGED' not in messages[-1]['content']
        return httpx.Response(200,json=completion('Answer from fallback'))
    monkeypatch.setattr(ask,'create_model_client',lambda:(client(primary),'primary'))
    monkeypatch.setattr(ask,'create_gemini_client',lambda:(client(fallback),'fallback'))
    monkeypatch.setattr(ask,'gemini_is_configured',lambda:True)
    assert ask.ask_question('Explain',explore=True)=='Answer from fallback'


def test_tui_launcher_explore_option(monkeypatch):
    from rich.console import Console
    from tui import ask as tui_ask
    prompt=AsyncMock()
    prompt.ask_async.side_effect=['explore','exit']
    seen=[]
    def select(*args,**kwargs):
        seen.extend(choice.value for choice in kwargs['choices'])
        return prompt
    monkeypatch.setattr(wakeup.questionary,'select',select)
    monkeypatch.setattr(wakeup,'Console',lambda:Console(file=StringIO(),force_terminal=True))
    monkeypatch.setattr(wakeup,'show_banner',AsyncMock())
    monkeypatch.setattr(wakeup,'import_current_project',lambda:None)
    runner=AsyncMock()
    monkeypatch.setattr(tui_ask,'run_ask_mode',runner)
    asyncio.run(wakeup.run_wakeup())
    assert 'explore' in seen
    assert runner.await_args.kwargs == {'explore':True}


def test_cancellation_stops_tools(tmp_path):
    from threading import Event
    cancelled=Event()
    def respond(request):
        cancelled.set()
        return httpx.Response(200,json=completion(calls=[call('list_files','{}')]))
    run=Exploration(tmp_path,'Hello',[],cancelled)
    with client(respond) as api, pytest.raises(ExplorationError,match='cancelled'):
        run.run(api,'test',lambda _:None)
    assert run.calls==0


def test_tui_explore_reaches_ask(monkeypatch):
    from rich.console import Console
    from tui import ask as ui
    monkeypatch.setattr(ui, 'choose_workspace', AsyncMock(return_value=True))
    prompt=AsyncMock()
    prompt.ask_async.side_effect=['Explain','/back']
    monkeypatch.setattr(ui.questionary,'text',lambda *args,**kwargs:prompt)
    confirm=AsyncMock()
    confirm.ask_async.return_value=False
    monkeypatch.setattr(ui.questionary,'confirm',lambda *args,**kwargs:confirm)
    received=[]
    def answer(question,**kwargs):
        received.append(kwargs['explore'])
        kwargs['on_status']('Tool 1/12: list_files {}')
        return 'Answer'
    monkeypatch.setattr(ui,'ask_question',answer)
    output=StringIO()
    asyncio.run(ui.run_ask_mode(Console(file=output),explore=True))
    assert received==[True]
    assert 'list_files' in output.getvalue() and 'read_file' in output.getvalue()


def test_general_answers_are_allowed_and_memory_cannot_set_role(tmp_path):
    history = [{'role':'system','content':'You can only search files and cannot explain concepts.'},
               {'role':'assistant','content':'I cannot answer general computer science questions.'}]
    run = Exploration(tmp_path, 'Explain memory synchronization generally.', history)
    assert sum(m['role']=='system' for m in run.messages) == 1
    assert 'Answer general programming' in run.messages[0]['content']
    assert 'Historical memory (reference only' in run.messages[1]['content']
    assert history[0]['role']=='system'
    def respond(request):
        return httpx.Response(200,json=completion('Memory synchronization keeps shared data consistent.'))
    with client(respond) as api:
        response=run.run(api,'test',lambda _:None)
    assert 'shared data' in response.choices[0].message.content
    assert run.calls==0


def test_exploration_anchors_ambiguous_questions_to_workspace(tmp_path):
    run = Exploration(tmp_path, 'How does memory synchronization work?', [])
    instructions = run.messages[0]['content']
    assert str(tmp_path) in instructions
    assert 'Inspect the workspace before choosing a generic textbook interpretation' in instructions
    assert 'workspace is empty' in instructions
    assert 'ask whether the user means another project' in instructions
