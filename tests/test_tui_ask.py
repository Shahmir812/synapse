from __future__ import annotations

import asyncio
from io import StringIO
from unittest.mock import AsyncMock

import pytest
from rich.console import Console

from tui import ask, wakeup
from synapse.ask import AskError
from synapse.model import ModelConfigError
from synapse.file_context import FileContextError


@pytest.fixture(autouse=True)
def no_attachments_by_default(monkeypatch):
    prompt = AsyncMock()
    prompt.ask_async.return_value = False
    monkeypatch.setattr(ask.questionary, "confirm", lambda *args, **kwargs: prompt)


def prompt_sequence(monkeypatch, module, method, values):
    prompt = AsyncMock()
    prompt.ask_async.side_effect = values
    monkeypatch.setattr(module.questionary, method, lambda *args, **kwargs: prompt)


def test_questions_render_and_back_returns(monkeypatch):
    prompt_sequence(monkeypatch, ask, "text", ["  Hello  ", "Second", "/back"])
    questions = []

    def answer(question, **kwargs):
        questions.append(question)
        return "**Hello** from the model"

    monkeypatch.setattr(ask, "ask_question", answer)
    output = StringIO()
    asyncio.run(ask.run_ask_mode(Console(file=output)))
    assert questions == ["Hello", "Second"]
    assert "Hello from the model" in output.getvalue()
    assert "**Hello**" not in output.getvalue()


@pytest.mark.parametrize("error", [AskError("Request timed out"), ModelConfigError("Set your model"), FileContextError("Cannot read attachment")])
def test_error_allows_retry_and_blank_input_is_skipped(monkeypatch, error):
    prompt_sequence(monkeypatch, ask, "text", [" ", "First", "Retry", None])
    questions = []

    def answer(question, **kwargs):
        questions.append(question)
        if len(questions) == 1:
            raise error
        return "Success"

    monkeypatch.setattr(ask, "ask_question", answer)
    output = StringIO()
    asyncio.run(ask.run_ask_mode(Console(file=output)))
    assert questions == ["First", "Retry"]
    assert "Please enter a question" in output.getvalue()
    assert str(error) in output.getvalue()
    assert "Success" in output.getvalue()


@pytest.mark.parametrize("exit_choice", ["exit", None])
def test_wakeup_launches_ask_and_returns_to_menu(monkeypatch, exit_choice):
    console = Console(file=StringIO(), force_terminal=True)
    monkeypatch.setattr(wakeup, "Console", lambda: console)
    prompt_sequence(monkeypatch, wakeup, "select", ["ask", exit_choice])
    run_ask = AsyncMock()
    monkeypatch.setattr(ask, "run_ask_mode", run_ask)
    asyncio.run(wakeup.run_wakeup())
    run_ask.assert_awaited_once_with(console)


def test_tui_selects_multiple_files_and_resets_each_question(monkeypatch):
    prompt_sequence(monkeypatch, ask, 'text', ['First', 'Second', '/back'])
    prompt_sequence(monkeypatch, ask, 'confirm', [True, False])
    prompt_sequence(monkeypatch, ask, 'checkbox', [['a.py', 'b.py']])
    monkeypatch.setattr(ask, 'selectable_files', lambda *args: (['a.py', 'b.py'], False))
    requests = []
    def answer(question, **kwargs):
        requests.append((question, kwargs['files']))
        return 'Answer'
    monkeypatch.setattr(ask, 'ask_question', answer)
    asyncio.run(ask.run_ask_mode(Console(file=StringIO())))
    assert requests == [('First', ['a.py', 'b.py']), ('Second', [])]


@pytest.mark.parametrize('step', ['confirm', 'checkbox', 'no_files'])
def test_cancel_attachment_does_not_send(monkeypatch, step):
    prompt_sequence(monkeypatch, ask, 'text', ['Question', '/back'])
    prompt_sequence(monkeypatch, ask, 'confirm', [None if step == 'confirm' else True])
    prompt_sequence(monkeypatch, ask, 'checkbox', [None])
    monkeypatch.setattr(ask, 'selectable_files', lambda *args: ([] if step == 'no_files' else ['a.py'], False))
    monkeypatch.setattr(ask, 'ask_question', lambda *args, **kwargs: pytest.fail('Cancelled request sent'))
    asyncio.run(ask.run_ask_mode(Console(file=StringIO())))


def test_empty_selection_sends_question_only(monkeypatch):
    prompt_sequence(monkeypatch, ask, 'text', ['Question', '/back'])
    prompt_sequence(monkeypatch, ask, 'confirm', [True])
    prompt_sequence(monkeypatch, ask, 'checkbox', [[]])
    monkeypatch.setattr(ask, 'selectable_files', lambda *args: (['a.py'], True))
    def answer(question, **kwargs):
        assert kwargs['files'] == []
        return 'Answer'
    monkeypatch.setattr(ask, 'ask_question', answer)
    output = StringIO()
    asyncio.run(ask.run_ask_mode(Console(file=output)))
    assert 'File list limited' in output.getvalue()
