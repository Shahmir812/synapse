"""Ask with optional project-scoped conversation memory."""

from __future__ import annotations

from collections.abc import Callable
from time import monotonic
from pathlib import Path
from synapse.memory import Conversation, MemoryError
from synapse.local_memory import LocalMemoryError
from synapse.exploration import Exploration, ExplorationError, inspect_workspace
from synapse.project_registry import active_workspace
from synapse.file_context import load_attachments, attach_to_question

from openai import APIConnectionError, APIError, APIStatusError, APITimeoutError, AuthenticationError, RateLimitError

from synapse.model import (
    GEMINI_BASE_URL, OPENROUTER_BASE_URL, ModelConfigError,
    create_gemini_client, create_model_client, gemini_is_configured,
)


class AskError(Exception):
    """An Ask request could not produce an answer."""


def ask_question(question: str, *, files: list[str | Path] | None = None, conversation: Conversation | None = None, explore: bool = False, expected_workspace: Path | None = None, cancel_event=None, on_status: Callable[[str], None] | None = None) -> str:
    question = question.strip()
    if not question:
        raise AskError("Question must not be empty.")

    report = on_status or (lambda message: None)
    workspace = active_workspace()
    if expected_workspace is not None and workspace != Path(expected_workspace).resolve():
        raise AskError('Active workspace changed. Choose the project again before sending this question.')
    if explore:
        try:
            inspect_workspace(workspace)
        except ExplorationError as error:
            raise AskError(str(error)) from error
    attachments = load_attachments(files or [], workspace)
    if attachments:
        report(f"Sending {len(attachments)} file(s) as context to the model (including fallback if needed):")
        for item in attachments:
            report(f"  {item.path} ({item.size} bytes)")
    original_question = question
    history = conversation.context(question) if conversation else []
    if conversation:
        if not conversation.temporary:
            report(conversation.status)
        report(f"Memory: {'temporary' if conversation.temporary else 'project conversation'} | conversation: {conversation.id}")
        report(f"Loaded {len(history)} context messages; file attachments are not stored as messages.")
    question = attach_to_question(question, attachments)
    exploration = Exploration(workspace, question, history, cancel_event) if explore else None
    if explore:
        report(f"Workspace exploration enabled: {workspace} (read-only)")
    # Keep the same memory snapshot through provider fallback.
    def request(provider, factory, endpoint, model_setting, key_setting):
        if exploration is not None:
            return _request(question, report, provider, factory, endpoint, model_setting, key_setting, history, exploration)
        if history:
            return _request(question, report, provider, factory, endpoint, model_setting, key_setting, history)
        return _request(question, report, provider, factory, endpoint, model_setting, key_setting)
    try:
        answer = request("OpenRouter", create_model_client, OPENROUTER_BASE_URL, "OPENROUTER_DEFAULT_MODEL", "OPENROUTER_API_KEY")
    except ExplorationError as error:
        raise AskError(str(error)) from error
    except (AskError, ModelConfigError) as primary_error:
        if not gemini_is_configured():
            raise
        report(f"OpenRouter failed: {primary_error}")
        report("Falling back to Google AI Studio…")
        try:
            answer = request("Google AI Studio", create_gemini_client, GEMINI_BASE_URL, "GEMINI_MODEL", "GEMINI_API_KEY")
        except ExplorationError as error:
            raise AskError(str(error)) from error
        except (AskError, ModelConfigError) as fallback_error:
            raise AskError(f"Both providers failed. OpenRouter: {primary_error} Google AI Studio: {fallback_error}") from fallback_error

    if cancel_event is not None and cancel_event.is_set():
        raise AskError("Ask request cancelled.")
    if conversation:
        try:
            conversation.save(original_question, answer)
            report("Memory: exchange kept for this temporary session." if conversation.temporary else conversation.status)
        except (MemoryError, LocalMemoryError) as error:
            report(f"Warning: answer generated, but memory save was not confirmed. {error}")
    return answer


def _request(question, report, provider, factory, endpoint, model_setting, key_setting, history=None, exploration=None) -> str:
    client, model = factory()
    started = monotonic()
    report(f"{provider}: trying model {model} ({model_setting})")
    report(f"Endpoint: {endpoint} | timeout: 30s | automatic retries: 0")
    try:
        with client:
            response = exploration.run(client, model, report) if exploration is not None else client.chat.completions.create(
                model=model,
                messages=(history or []) + [{"role": "user", "content": question}],
            )
    except AuthenticationError as error:
        raise AskError(f"{provider} authentication failed (HTTP 401, model {model}). Check {key_setting}.") from error
    except APITimeoutError as error:
        raise AskError(f"{provider} model {model}: request timed out after {monotonic() - started:.1f}s. Check your connection or try again.") from error
    except RateLimitError as error:
        raise AskError(f"{provider} rate limit reached (HTTP 429, model {model}). Please try again later.") from error
    except APIConnectionError as error:
        raise AskError(f"Could not connect to {provider} at {endpoint} (model {model}). Check your network, proxy, or DNS settings.") from error
    except APIStatusError as error:
        if error.status_code == 402 and provider == "OpenRouter":
            raise AskError(
                f"OpenRouter reported insufficient credits (HTTP 402, model {model}). Add credits at https://openrouter.ai/settings/credits or configure a model your account can use."
            ) from error
        guidance = {
            400: "Check that the model supports this request.",
            403: "Check your API key permissions and account restrictions.",
            404: f"Check {model_setting}; the model or endpoint may be unavailable.",
            502: "The upstream model provider failed. Try again later.",
            503: "The model is unavailable. Try again later or configure another model.",
        }.get(error.status_code, "Check your model setting and try again.")
        raise AskError(f"{provider} could not complete the request (HTTP {error.status_code}, model {model}). {guidance}") from error
    except APIError as error:
        raise AskError(f"{provider} could not complete the request (model {model}). Check {model_setting} and try again.") from error

    answer = response.choices[0].message.content if response.choices else None
    if not answer or not answer.strip():
        raise AskError(f"The model returned no answer ({provider}, model {model}). Please try again.")
    actual_model = response.model or model
    report(f"{provider}: response from {actual_model} in {monotonic() - started:.1f}s")
    if response.usage:
        report(f"Tokens: {response.usage.prompt_tokens} input, {response.usage.completion_tokens} output")
    return answer.strip()
