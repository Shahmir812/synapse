# synapse

Synapse is a Python CLI for building an AI coding-agent workflow.

Synapse provides a command-line interface, local project profiles, and a basic Ask command.
Project profiles establish the workspace and memory namespace that later agent
modes will use.

## Setup

```bash
python -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -e ".[dev]"
```

Copy `.env.example` to `.env` and fill in local keys as features need them.

## Run

```bash
python -m synapse wakeup
```

The interactive launcher shows an animated shadowed Synapse banner, workspace
and configured model details, and a purple menu. On narrow terminals it uses a
compact banner. Set `SYNAPSE_NO_ANIMATION=1` to skip the startup animation.

In an interactive terminal, choose **Ask Mode**, type a question, and press Enter.
Answers render as Markdown, with a progress indicator while the model responds.
You can ask more questions or type `/back` to return to the menu, then choose
**Exit**. Ctrl+C at a prompt also goes back or exits. Each question is independent.
When output is redirected, `wakeup` only prints the banner.

Initialize the current folder as a Synapse project:

```bash
python -m synapse project init
```

Inspect or list saved profiles:

```bash
python -m synapse project status
python -m synapse project list
```

Project state is stored locally in `.synapse/projects.json` and is not committed.

## Ask a question

Set these values in your local `.env` file or environment:

```dotenv
OPENROUTER_API_KEY=your-openrouter-api-key
OPENROUTER_DEFAULT_MODEL=your-openrouter-model-id
```

Use an explicit model ID available to your OpenRouter account, then run:

```bash
python -m synapse ask "Explain Python decorators with an example"
```

Ask sends a single question to OpenRouter and displays the answer as Markdown in
the terminal. Each request is independent: Ask does not read project files or
save conversation history. A project profile is not required.

Empty questions, missing configuration, authentication failures, timeouts, and
other provider failures produce a readable error and a nonzero exit status for
the direct command. Interactive Ask displays the error and lets you try again.
Both interfaces show the requested model, OpenRouter endpoint, timeout, and retry
settings before sending a request. Successful responses show the returned model,
elapsed time, and token usage when provided. Failures include the HTTP status
when available and model-specific troubleshooting guidance. API keys are never
printed. The direct command writes diagnostics to stderr, keeping stdout for
the answer.

Requests use a 30-second SDK timeout with automatic retries disabled.
The `project` and `wakeup` commands do not require model configuration.
The other settings in `.env.example` are reserved for future features.

## Google AI Studio fallback

To enable the optional direct Gemini fallback, set these in your local `.env`:

```dotenv
GEMINI_API_KEY=your-google-ai-studio-api-key
GEMINI_MODEL=your-gemini-model-id
```

Use a model ID available to your Google AI Studio account, without OpenRouter's
`google/` prefix. `GOOGLE_API_KEY` is also accepted; `GEMINI_API_KEY` takes
precedence when both are set. The Google key is separate from your OpenRouter key.

Both the direct Ask command and TUI try OpenRouter first. If configuration is
missing, the request fails, or the response is empty, Synapse sends the same
question directly to Google AI Studio when a Google key is configured. Each
provider is attempted at most once, with a 30-second SDK timeout per provider.
There is no fallback after a successful OpenRouter answer.

Diagnostics identify the failed provider, the switch to Google, and the model
that answered. If both fail, the error summarizes both failures. Without a Google
key, only OpenRouter is used. Setting a Google key requires `GEMINI_MODEL` as well.

This uses Google's documented [OpenAI-compatible Gemini endpoint](https://ai.google.dev/gemini-api/docs/openai)
through the existing SDK; no additional dependency is needed.

## Test

```bash
python -m pytest -q
```

Ask tests use mocked HTTP responses; they require no API keys or paid requests.
