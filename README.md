# Synapse

**An AI coding assistant for your terminal, built one working milestone at a time.**

Synapse is being built to help developers move from a question to a plan to a
reviewed code change, with the project context carried through that workflow.
The goal is a workspace-aware assistant that can explore code, remember project
decisions, use tools, and help implement changes while keeping the developer in
control.

Today, Synapse has a working terminal interface, local project profiles, and real
AI-powered Ask requests with OpenRouter and a Google AI Studio fallback. This
repository is an incremental build toward the larger coding-agent workflow.

## The vision

Working on a codebase involves more than generating code. You need to understand
what is already there, decide how a change should fit, carry out the work, and
review the result. Synapse aims to bring those steps into one terminal workflow:

**Understand → Plan → Implement → Review**

- **Understand the project.** Ask questions grounded in the actual files,
  structure, and constraints of a workspace.
- **Turn a goal into a plan.** Break a task into steps that the developer can
  inspect and choose before execution.
- **Act with review built in.** Let an agent propose file changes and tool
  actions, show the relevant diffs, and request approval before applying changes.
- **Maintain continuity.** Keep useful context and decisions scoped to a project
  so future sessions can pick up where earlier work left off.
- **Support different ways of working.** Start in the terminal, then extend the
  same core capabilities to coordinated workflows and a Telegram interface.

These are the intended capabilities. The current Ask mode answers the question
you supply and can use text files you explicitly attach. It does not yet explore
your codebase on its own, edit files, or remember earlier questions.

## What works today

| Capability | Current behavior |
| --- | --- |
| Terminal launcher | Animated Synapse banner, workspace and configured model details, and an interactive Ask menu |
| Project profiles | Initialize a workspace, inspect and list local profiles, and select an existing profile |
| Ask from the CLI or TUI | Attach multiple workspace text files, send a question to a real model, and render its answer as Markdown in the terminal |
| Provider fallback | Try OpenRouter first, then Google AI Studio when configured and needed |
| Request visibility | Show the requested and responding models, endpoint, elapsed time, token usage when available, and readable failures |
| Automated verification | Test requests and failure paths with mocked HTTP responses, without API charges |

Answers and conversation history are not saved. Each Ask request is independent.
Project metadata is stored locally, separately from model requests.

## Building principles

- **Make behavior visible.** Show which provider and model are being used, what
  failed, and when a fallback takes over.
- **Keep the developer in control.** As tools arrive, make proposed actions and
  their effects reviewable.
- **Respect project boundaries.** Build future tools and memory around an explicit
  workspace and project identity.
- **Keep provider choice flexible.** Separate the interface from the model client
  so the workflow can grow beyond a single provider.

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
When output is redirected, `wakeup` only prints a short wakeup message.

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
the terminal. Each request is independent: Ask reads only the files you explicitly
attach and does not save conversation history. A project profile is not required.

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

## Ask about multiple files

Repeat `--file` to attach files to a single question:

```bash
python -m synapse ask "How do these modules work together?" \
  --file synapse/ask.py \
  --file synapse/model.py
```

Paths are relative to the current working directory, which defines the workspace.
Absolute paths are accepted only when they resolve inside that workspace. Quote
paths containing spaces. Repeated paths to the same file are included only once.

In TUI Ask Mode, enter your question, choose **Yes** at **Attach workspace files?**,
then use **Space** to toggle files and **Enter** to submit the selection. Choose
**No**, or submit an empty selection, to ask without files. Cancelling an
attachment prompt returns to the question prompt without sending a request.
Selections apply only to the current question.

Before the request, Synapse prints every attached path and its size. The selected
files' contents are sent with your question to OpenRouter and, if needed, to the
Google AI Studio fallback. Both receive the same snapshot. Answers are still
rendered only in the terminal; no answer or attachment history is saved.

Attachments must be UTF-8 text: at most 10 files, 64 KiB per file, and 256 KiB
combined. Missing, unreadable, binary, and out-of-workspace files are rejected
before contacting either provider. Common sensitive paths such as `.env*`,
private-key files, and credential files are excluded, along with Git metadata,
local Synapse state, and dependency/build directories. These path exclusions are
not a secret-content detector; select only files you intend to share.

The picker skips hidden directories and symlinks, and shows up to 500 eligible
files after scanning at most 5,000 file entries. If the list is limited, use the
CLI with explicit `--file` paths. Explicit attachments are validated again when
the question is submitted.

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
