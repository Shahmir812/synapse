"""Agent task entry and per-action review, on the terminal's asyncio event loop."""
import asyncio
from concurrent.futures import TimeoutError as FutureTimeout
import json
import sys
from threading import Event

import questionary
from rich.markdown import Markdown
from rich.panel import Panel
from rich.syntax import Syntax

from synapse.agent import AgentError
from synapse.ask import ask_question, AskError
from synapse.model import ModelConfigError
from synapse.memory import MemoryError
from synapse.local_memory import LocalMemoryError
from synapse.projects import ProjectError
from synapse.file_context import FileContextError
from synapse.project_registry import active_workspace
from tui.ask import cancellable_ask
from tui.projects import choose_workspace
from tui.conversations import choose_conversation
from tui.theme import ACCENT, PROMPT_STYLE


def show_proposal(console, proposal):
    console.print(f'Proposed action: {proposal.kind}', style='bold', markup=False)
    console.print(f'Workspace: {proposal.workspace}', markup=False)
    console.print(f'Reason: {proposal.reason}', markup=False)
    if proposal.kind == 'run_command':
        console.print(f'Arguments: {json.dumps(proposal.argv, ensure_ascii=False)}', markup=False)
        console.print(f'Timeout: {proposal.timeout}s', markup=False)
        console.print('Commands run with your user permissions and can access files outside the workspace or the network.', style='yellow')
    else:
        console.print(f'File: {proposal.path}', markup=False)
        console.print(Syntax(proposal.diff or '(empty file)', 'diff', word_wrap=True))


def cli_approval(console):
    def approve(proposal):
        show_proposal(console, proposal)
        if not sys.stdin.isatty() or not console.is_terminal:
            console.print('Action denied: interactive review requires a terminal.', style='yellow')
            return False
        try:
            return console.input('Approve this action? [y/N] ').strip().lower() in ('y', 'yes')
        except EOFError:
            return False
    return approve


async def run_task(console, task, workspace, conversation):
    loop = asyncio.get_running_loop()
    cancellation = Event()

    async def review(proposal):
        show_proposal(console, proposal)
        answer = await questionary.select('Review action', choices=[
            questionary.Choice('Reject action', value='reject'),
            questionary.Choice('Approve once', value='approve'),
            questionary.Choice('Stop this run', value='stop'),
        ], style=PROMPT_STYLE).ask_async()
        if answer in (None, 'stop'):
            cancellation.set()
        return answer == 'approve'

    def approve(proposal):
        if cancellation.is_set():
            raise AgentError('Agent run cancelled.')
        future = asyncio.run_coroutine_threadsafe(review(proposal), loop)
        while True:
            try:
                return future.result(timeout=0.1)
            except FutureTimeout:
                if cancellation.is_set():
                    future.cancel()
                    raise AgentError('Agent run cancelled.')

    # Approval prompts own the terminal; do not keep a live spinner over them.
    return await cancellable_ask(
        ask_question, task, agent=True, approve=approve, conversation=conversation,
        expected_workspace=workspace, cancel_event=cancellation,
        on_status=lambda message: console.print(message, style='dim', markup=False),
    )


async def run_agent_mode(console):
    console.print(Panel(
        'Describe a coding task. Synapse will inspect, propose changes, and run approved checks.\n'
        'Review each diff and command. /project changes workspace; /conversation changes memory; /back returns.\n'
        'Ctrl+C stops the run. Changes already applied remain on disk.',
        title='Agent Mode', border_style=ACCENT,
    ))
    while True:
        if not await choose_workspace(console):
            return
        workspace = active_workspace()
        conversation = await choose_conversation(console)
        if conversation is None:
            return
        console.print(f'Conversation: {conversation.id}', style='dim', markup=False)
        while True:
            task = await questionary.text('Agent task:', style=PROMPT_STYLE).ask_async()
            if task is None or task.strip().lower() == '/back':
                return
            task = task.strip()
            if task.lower() == '/project':
                break
            if task.lower() == '/conversation':
                selected = await choose_conversation(console)
                if selected is not None:
                    conversation = selected
                    console.print(f'Conversation: {conversation.id}', style='dim', markup=False)
                continue
            if not task:
                console.print('Please describe a task.', style='yellow')
                continue
            try:
                answer = await run_task(console, task, workspace, conversation)
            except (AskError, AgentError, ModelConfigError, MemoryError, LocalMemoryError, ProjectError, FileContextError) as error:
                console.print(f'Agent stopped: {error}', style='red', markup=False)
                continue
            console.print(Panel(Markdown(answer), title='Agent result', border_style=ACCENT))
