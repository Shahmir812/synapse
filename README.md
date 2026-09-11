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
your codebase on its own or edit files. Honcho can retain and resume project conversations.

## What works today

| Capability | Current behavior |
| --- | --- |
| Terminal launcher | Animated Synapse banner, workspace and configured model details, and an interactive Ask menu |
| Project profiles | Create, switch, rename, and inspect projects from the CLI or TUI; share the active workspace across launches |
| Ask from the CLI or TUI | Attach multiple workspace text files, send a question to a real model, and render its answer as Markdown in the terminal |
| Provider fallback | Try OpenRouter first, then Google AI Studio when configured and needed |
| Request visibility | Show the requested and responding models, endpoint, elapsed time, token usage when available, and readable failures |
| Automated verification | Test requests and failure paths with mocked HTTP responses, without API charges |

Honcho conversations persist questions and answers. Temporary TUI conversations
keep recent context only until you leave Ask mode.
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
**Exit**. Ctrl+C at a prompt also goes back or exits. Use `/conversation` to start or resume a conversation.
When output is redirected, `wakeup` only prints a short wakeup message.

Register the current folder as a Synapse project:

```bash
python -m synapse project init
```

Inspect or list saved profiles:

```bash
python -m synapse project status
python -m synapse project list
```

The launcher has one **Manage projects** submenu containing Create, Switch,
Details, Rename, Delete, and Back.

**Create project** asks for a name and creates its workspace at
`CORE_DIR/Projects/<name>`. `CORE_DIR` defaults to the Synapse application root
(the folder containing `main.py` in this checkout). Set `SYNAPSE_CORE_DIR` to
choose a different base folder. The `Projects` directory is created if needed.
Whitespace becomes underscores: `My Project` becomes `Projects/My_Project`.
Dots and underscores already in the name are preserved. Path separators and
hidden/traversal names are rejected. Existing folders are never overwritten.

```bash
python -m synapse project create "My Project"
python -m synapse project remove PROJECT_ID
```

**Delete project from registry** removes only the saved profile, after confirmation
in the TUI. The workspace and all its files remain on disk. Removing the active
project selects another saved project (preferring an existing workspace); removing
the last one clears selection. Removed legacy profiles are not automatically
re-imported. You can explicitly register their folders again with `project init`.
Renaming changes only the display name, preserving the workspace path and identity.
Existing projects remain in their original locations. `project init` remains the
way to register an existing folder without creating or moving it.

Register another workspace or select and rename a saved project from the CLI:

```bash
python -m synapse project init ~/code/my-app --name "My App"
python -m synapse project list
python -m synapse project use PROJECT_ID
python -m synapse project rename "New name"
```

Project profiles and the active selection are saved in
`~/.synapse/projects.json`. Set `SYNAPSE_PROJECTS_FILE` to use a different registry
file (tests use an isolated temporary registry). Selection persists across
launches and working directories. Each workspace can be registered once; renaming
preserves its ID, creation time, memory namespace, and memory revision.

Existing workspace-local `.synapse/projects.json` profiles are imported when you
launch the TUI from that workspace or register its folder. IDs and memory
namespaces are preserved, and the original file is left in place. Importing a
legacy workspace on startup does not replace an existing active selection.
Conflicting legacy profiles are reported instead of being silently replaced.

A selected project's folder must still exist before it can be used for Ask or
selected again. If it has moved or been deleted, select another valid project.
With no saved projects, Ask uses the current working directory. Corrupt registry
files produce an error instead of silently changing the workspace.

Memory namespaces define isolated Honcho workspaces. Switching projects changes
which conversations are available; renaming preserves access to existing memory.
Removing a project from the local registry does not delete its remote Honcho memory.

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
the terminal. Ask reads only files you explicitly attach. Honcho memory requires
an active project; temporary requests do not.

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

Paths are relative to the active project workspace. Without a selected project,
the current working directory defines the workspace. The TUI file picker uses
that same workspace, even when Synapse was launched from a different folder.
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
rendered in the terminal. Raw attachment contents are omitted from the user
messages stored in Honcho, but saved answers can contain file excerpts. Reattach
files when a later question needs their contents.

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

## Honcho conversation memory

Install the updated dependencies with `python -m pip install -e ".[dev]"`, then
set your key in `.env`:

```dotenv
HONCHO_API_KEY=your-honcho-key
# Optional: a self-hosted Honcho API URL
# HONCHO_URL=https://api.honcho.dev
```

Use a key that can access/create project workspaces. Synapse derives a separate
Honcho workspace from each project's stable ID, memory namespace, and revision.
It intentionally does not use a shared `HONCHO_WORKSPACE_ID`. Conversations live
in Honcho, while the local project registry stays in `~/.synapse/projects.json`.
SQLite at `~/.synapse/memory.sqlite3` stores completed exchanges, cached context,
and a pending-upload queue. Override this path with `SYNAPSE_MEMORY_DB`.

In TUI Ask, choose **New conversation**, **Resume conversation**, or **Temporary
conversation**. Resume lists sessions only from the active project, with page
navigation. Use `/conversation` during Ask to change conversations. Returning to
the launcher discards temporary history; saved Honcho sessions remain resumable.
With an active project, conversations are saved locally even without a Honcho key.
They are queued for upload once Honcho is configured. Without a selected project,
TUI Ask uses a temporary conversation.

```bash
# With Honcho configured, start a new conversation and print its ID
python -m synapse ask "For this project, keep changes small" --new-conversation

# Use the ID shown in the previous request's diagnostics
python -m synapse ask "What approach did we agree on?" --conversation SESSION_ID

# Explicitly skip persistent memory
python -m synapse ask "Explain decorators" --temporary
```

With an active project, plain CLI Ask starts a new conversation saved locally;
use `--conversation` for follow-ups. `--new-conversation` works offline too.
Use `--temporary` to opt out of persistent storage and uploads. Without a project
or a configured Honcho key, plain CLI Ask remains an independent request.

Before generation, Synapse requests up to 3,000 tokens of session context,
including summaries, recent messages, and relevant user representation. It also
applies a 16,000-character ceiling locally. The same snapshot goes to OpenRouter
and to Gemini if fallback is needed. Memory is supporting context, not a source
of guaranteed facts or current file contents.

After a successful answer, Synapse writes the original question and answer to
SQLite in one transaction before attempting upload. Raw file attachments are
omitted; saved answers can still contain excerpts. Failed model requests do not
create completed exchanges. If Honcho is offline, Ask uses cached local context
and continues saving exchanges locally across restarts. Remote summaries and
recent context retrieved while online are cached for offline use; this cache is
bounded, so it is not a complete mirror of historical remote-only conversations.

Pending uploads are retried before the next question in that conversation and
after saving an answer. To synchronize all queued conversations in the active
project, use **Sync pending project memory** in the conversation menu or run:

```bash
python -m synapse memory status
python -m synapse memory sync
```

There is no background daemon: synchronization runs while you use these controls
or Ask. Status messages distinguish local/offline storage, pending uploads, and
completed synchronization. Local history remains after successful upload.
Removing a project profile deletes neither its local memory nor its remote memory.

Every exchange has a unique ID stored in Honcho message metadata. Sync workers
sharing one database are serialized with a local file lock (POSIX platforms).
Before upload, Synapse looks for the exchange's user and assistant messages,
checking their contents, and skips confirmed messages. This supports recovery
when Honcho accepted an exchange but the response was lost.

Honcho does not currently document atomic idempotency keys for ingestion. If an
attempt was marked as uploading but its messages cannot be confirmed remotely,
Synapse retains it locally and does not automatically resend it. This prevents
blind retries but means unresolved uploads can require investigation; later
exchanges stay queued behind them. Multiple machines with independent database
copies are not a supported concurrent synchronization configuration.

A local database failure is reported explicitly. A generated answer is still
shown if its save fails, but Synapse does not claim it was persisted. Temporary
conversations never write to SQLite or Honcho.

Honcho calls use a 10-second SDK timeout and zero automatic retries. One logical
operation may involve multiple SDK requests. This integration follows Honcho's
[session context API](https://honcho.dev/docs/v3/documentation/features/get-context).

## Test

```bash
python -m pytest -q
```

Ask tests use mocked HTTP responses; they require no API keys or paid requests.
