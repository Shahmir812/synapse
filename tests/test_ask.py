from __future__ import annotations

import json
import sys

import httpx
import pytest
from openai import OpenAI

import main
from synapse import model


@pytest.fixture(autouse=True)
def isolated_environment(monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
    monkeypatch.delenv("GEMINI_MODEL", raising=False)
    monkeypatch.setattr(main, "load_dotenv", lambda: None)
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
    monkeypatch.setenv("OPENROUTER_DEFAULT_MODEL", "test-model")

    def unexpected_client(**kwargs):
        pytest.fail("Unexpected model client construction")

    monkeypatch.setattr(model, "OpenAI", unexpected_client)


def mock_provider(monkeypatch, handler):
    def create_client(**kwargs):
        return OpenAI(**kwargs, http_client=httpx.Client(transport=httpx.MockTransport(handler)))

    monkeypatch.setattr(model, "OpenAI", create_client)


def run_cli(monkeypatch, *args):
    monkeypatch.setattr(sys, "argv", ["synapse", *args])
    main.main()


def test_ask_sends_question_and_renders_markdown(monkeypatch, capsys):
    def respond(request):
        assert str(request.url) == "https://openrouter.ai/api/v1/chat/completions"
        assert request.headers["authorization"] == "Bearer test-key"
        payload = json.loads(request.content)
        assert payload["model"] == "test-model"
        assert payload["messages"] == [{"role": "user", "content": "Explain decorators"}]
        assert "tools" not in payload
        return httpx.Response(200, json={
            "id": "test", "object": "chat.completion", "created": 0,
            "model": "resolved-model",
            "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
            "choices": [{"index": 0, "finish_reason": "stop",
                         "message": {"role": "assistant", "content": "**Decorators** wrap functions."}}],
        })

    mock_provider(monkeypatch, respond)
    run_cli(monkeypatch, "ask", "  Explain decorators  ")
    output = capsys.readouterr()
    assert "Decorators" in output.out
    assert "**Decorators**" not in output.out
    assert "trying model test-model" in output.err
    assert "response from resolved-model" in output.err
    assert "Tokens: 10 input, 5 output" in output.err
    assert "test-key" not in output.err


@pytest.mark.parametrize("question", ["", "   ", "\n\t"])
def test_empty_question_does_not_create_client(monkeypatch, capsys, question):
    with pytest.raises(SystemExit) as error:
        run_cli(monkeypatch, "ask", question)
    assert error.value.code == 1
    assert "Question must not be empty" in capsys.readouterr().err


def test_missing_question_shows_usage(monkeypatch, capsys):
    with pytest.raises(SystemExit) as error:
        run_cli(monkeypatch, "ask")
    assert error.value.code == 2
    assert "question" in capsys.readouterr().err


@pytest.mark.parametrize("name", ["OPENROUTER_API_KEY", "OPENROUTER_DEFAULT_MODEL"])
@pytest.mark.parametrize("value", [None, "   "])
def test_missing_configuration(monkeypatch, capsys, name, value):
    if value is None:
        monkeypatch.delenv(name)
    else:
        monkeypatch.setenv(name, value)
    with pytest.raises(SystemExit) as error:
        run_cli(monkeypatch, "ask", "Hello")
    assert error.value.code == 1
    assert name in capsys.readouterr().err


@pytest.mark.parametrize("status, expected", [
    (401, "authentication failed"),
    (402, "insufficient credits"),
    (429, "rate limit reached"),
    (500, "could not complete the request"),
])
def test_provider_errors_are_readable(monkeypatch, capsys, status, expected):
    requests = []

    def respond(request):
        requests.append(request)
        return httpx.Response(status, json={"error": {"message": "private provider detail"}})

    mock_provider(monkeypatch, respond)
    with pytest.raises(SystemExit) as error:
        run_cli(monkeypatch, "ask", "Hello")
    assert error.value.code == 1
    output = capsys.readouterr()
    assert expected in output.err
    assert f"HTTP {status}" in output.err
    assert "test-model" in output.err
    assert "private provider detail" not in output.err
    assert not output.out
    assert len(requests) == 1


@pytest.mark.parametrize("exception, expected", [
    (httpx.ReadTimeout, "timed out"),
    (httpx.ConnectError, "Could not connect"),
])
def test_transport_errors(monkeypatch, capsys, exception, expected):
    def respond(request):
        raise exception("transport detail", request=request)

    mock_provider(monkeypatch, respond)
    with pytest.raises(SystemExit) as error:
        run_cli(monkeypatch, "ask", "Hello")
    assert error.value.code == 1
    assert expected in capsys.readouterr().err


@pytest.mark.parametrize("choices", [[], [{"message": {"role": "assistant", "content": " "}}]])
def test_empty_model_answer(monkeypatch, capsys, choices):
    mock_provider(monkeypatch, lambda request: httpx.Response(200, json={"choices": choices}))
    with pytest.raises(SystemExit) as error:
        run_cli(monkeypatch, "ask", "Hello")
    assert error.value.code == 1
    assert "returned no answer" in capsys.readouterr().err


def test_existing_commands_work_without_model_configuration(monkeypatch, tmp_path, capsys):
    monkeypatch.delenv("OPENROUTER_API_KEY")
    monkeypatch.delenv("OPENROUTER_DEFAULT_MODEL")
    run_cli(monkeypatch, "wakeup")
    assert "Synapse is awake" in capsys.readouterr().out
    run_cli(monkeypatch, "project", "init", str(tmp_path), "--name", "Demo")
    run_cli(monkeypatch, "project", "status", str(tmp_path))
    assert "Demo" in capsys.readouterr().out


@pytest.mark.parametrize("primary_failure", [402, 429, 500, "timeout", "empty", "config"])
def test_google_fallback(monkeypatch, capsys, primary_failure):
    monkeypatch.setenv("GEMINI_API_KEY", "google-test-key")
    monkeypatch.setenv("GEMINI_MODEL", "gemini-test")
    if primary_failure == "config":
        monkeypatch.delenv("OPENROUTER_API_KEY")
    hosts = []

    def respond(request):
        hosts.append(request.url.host)
        if request.url.host == "openrouter.ai":
            assert request.headers["authorization"] == "Bearer test-key"
            if primary_failure == "timeout":
                raise httpx.ReadTimeout("timeout", request=request)
            if primary_failure == "empty":
                return httpx.Response(200, json={"choices": []})
            return httpx.Response(primary_failure, json={"error": {"message": "failed"}})
        assert request.url.host == "generativelanguage.googleapis.com"
        assert request.url.path == "/v1beta/openai/chat/completions"
        assert request.headers["authorization"] == "Bearer google-test-key"
        payload = json.loads(request.content)
        assert payload["model"] == "gemini-test"
        assert payload["messages"] == [{"role": "user", "content": "Hello"}]
        return httpx.Response(200, json={"model": "gemini-test", "choices": [{"message": {"role": "assistant", "content": "**Google answer**"}}]})

    mock_provider(monkeypatch, respond)
    run_cli(monkeypatch, "ask", "Hello")
    output = capsys.readouterr()
    assert "Google answer" in output.out
    assert "Falling back to Google AI Studio" in output.err
    assert "Google AI Studio: response from gemini-test" in output.err
    assert "google-test-key" not in output.err
    assert hosts == (["openrouter.ai"] if primary_failure != "config" else []) + ["generativelanguage.googleapis.com"]


def test_both_providers_fail_once(monkeypatch, capsys):
    monkeypatch.setenv("GOOGLE_API_KEY", "google-test-key")
    monkeypatch.setenv("GEMINI_MODEL", "gemini-test")
    hosts = []

    def respond(request):
        hosts.append(request.url.host)
        return httpx.Response(401, json={"error": {"message": "private"}})

    mock_provider(monkeypatch, respond)
    with pytest.raises(SystemExit):
        run_cli(monkeypatch, "ask", "Hello")
    output = capsys.readouterr()
    assert "Both providers failed" in output.err
    assert "GEMINI_API_KEY" in output.err
    assert hosts == ["openrouter.ai", "generativelanguage.googleapis.com"]


def test_openrouter_success_skips_google(monkeypatch, capsys):
    monkeypatch.setenv("GEMINI_API_KEY", "google-test-key")
    def respond(request):
        assert request.url.host == "openrouter.ai"
        return httpx.Response(200, json={"model": "test-model", "choices": [{"message": {"role": "assistant", "content": "Hello"}}]})
    mock_provider(monkeypatch, respond)
    run_cli(monkeypatch, "ask", "Hello")
    assert "Falling back" not in capsys.readouterr().err


def test_fallback_requires_model(monkeypatch, capsys):
    monkeypatch.setenv("GEMINI_API_KEY", "google-test-key")
    mock_provider(monkeypatch, lambda request: httpx.Response(402, json={"error": {"message": "failed"}}))
    with pytest.raises(SystemExit):
        run_cli(monkeypatch, "ask", "Hello")
    assert "Set GEMINI_MODEL" in capsys.readouterr().err


def test_cli_multiple_files_reused_on_fallback(monkeypatch, capsys, tmp_path):
    monkeypatch.chdir(tmp_path)
    (tmp_path / 'a.py').write_text('from b import helper')
    (tmp_path / 'b.py').write_text('def helper(): return 42')
    monkeypatch.setenv('GEMINI_API_KEY', 'google-test-key')
    monkeypatch.setenv('GEMINI_MODEL', 'gemini-test')
    payloads = []
    def respond(request):
        payloads.append(json.loads(request.content))
        # The attachment list must have been printed before any network request.
        output = capsys.readouterr()
        if len(payloads) == 1:
            assert 'a.py' in output.err and 'b.py' in output.err
            assert 'from b import' not in output.err
            (tmp_path / 'a.py').write_text('changed during request')
            return httpx.Response(402, json={'error': {'message': 'credits'}})
        return httpx.Response(200, json={'model': 'gemini-test', 'choices': [{'message': {'role': 'assistant', 'content': 'They work together.'}}]})
    mock_provider(monkeypatch, respond)
    run_cli(monkeypatch, 'ask', 'How do these interact?', '--file', 'a.py', '--file', 'b.py')
    assert len(payloads) == 2
    assert payloads[0]['messages'] == payloads[1]['messages']
    content = payloads[0]['messages'][0]['content']
    assert 'How do these interact?' in content
    assert 'from b import helper' in content and 'def helper(): return 42' in content
    assert 'changed during request' not in content


def test_cli_bad_attachment_never_creates_client(monkeypatch, capsys, tmp_path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv('GEMINI_API_KEY', 'google-test-key')
    with pytest.raises(SystemExit) as error:
        run_cli(monkeypatch, 'ask', 'Explain', '--file', 'missing.py')
    assert error.value.code == 1
    assert 'must exist inside the workspace' in capsys.readouterr().err
