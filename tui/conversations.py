"""Conversation selection without hiding memory outages."""
import asyncio
import questionary
from synapse.memory import MemoryError, configured, conversations, open_conversation, sync_pending, project_scope
from synapse.projects import ProjectError
from synapse.local_memory import LocalMemoryError
from tui.theme import PROMPT_STYLE


async def choose_conversation(console):
    from synapse.project_registry import current_project
    if current_project() is None:
        console.print('No project selected. Using a temporary conversation.', style='yellow')
        return open_conversation(temporary=True)
    if not configured():
        console.print('Honcho is not configured. Conversations will be saved locally and queued for upload.', style='yellow')
    while True:
        action = await questionary.select('Conversation', choices=[
            questionary.Choice('New conversation (saved locally + Honcho)', value='new'),
            questionary.Choice('Resume conversation', value='resume'),
            questionary.Choice('Sync pending project memory', value='sync'),
            questionary.Choice('Temporary conversation (not saved)', value='temporary'),
            questionary.Choice('Back', value='back'),
        ], style=PROMPT_STYLE).ask_async()
        if action in (None, 'back'):
            return None
        if action == 'temporary':
            return open_conversation(temporary=True)
        try:
            if action == 'sync':
                with console.status('Syncing project memory…'):
                    status = await asyncio.to_thread(sync_pending, project_scope())
                console.print(status, markup=False)
                continue
            session_id = None
            if action == 'resume':
                page = 1
                while True:
                    with console.status('Loading conversations…'):
                        ids, more = await asyncio.to_thread(conversations, page)
                    choices = [questionary.Choice(value, value=value) for value in ids]
                    if more:
                        choices.append(questionary.Choice('Next page', value='next'))
                    if page > 1:
                        choices.append(questionary.Choice('Previous page', value='previous'))
                    choices.append(questionary.Choice('Back', value='back'))
                    if not ids:
                        console.print('No conversations on this page.', style='dim')
                    session_id = await questionary.select('Saved conversations', choices=choices, style=PROMPT_STYLE).ask_async()
                    if session_id == 'next':
                        page += 1
                    elif session_id == 'previous':
                        page -= 1
                    else:
                        break
                if session_id in (None, 'back'):
                    continue
            with console.status('Opening conversation…'):
                return await asyncio.to_thread(open_conversation, session_id)
        except (MemoryError, ProjectError, LocalMemoryError) as error:
            console.print(str(error), style='yellow', markup=False)
            console.print('Retry or select Temporary conversation to continue without saved memory.', style='dim')
