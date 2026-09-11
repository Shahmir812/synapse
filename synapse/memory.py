"""Project-isolated Honcho memory with durable SQLite fallback and synchronization."""
from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass, field
import hashlib
import os
from uuid import uuid4

import httpx
from honcho import Honcho

from synapse.project_registry import current_project
from synapse.projects import ProjectError
from synapse import local_memory as local

CONTEXT_TOKENS = 3000
MAX_CONTEXT_CHARS = 16000


class MemoryError(Exception):
    """Honcho is unavailable or a conversation cannot be resumed."""


def configured() -> bool:
    return bool(os.environ.get('HONCHO_API_KEY', '').strip())


def project_scope() -> str:
    project = current_project()
    if project is None:
        raise MemoryError('Select a project before using Honcho memory, or choose a temporary conversation.')
    identity = f'{project.id}:{project.memory_namespace}:{project.memory_revision}'
    return 'synapse-' + hashlib.sha256(identity.encode()).hexdigest()[:32]


@contextmanager
def honcho_client(scope):
    key = os.environ.get('HONCHO_API_KEY', '').strip()
    if not key:
        raise MemoryError('Set HONCHO_API_KEY to enable memory, or choose a temporary conversation.')
    try:
        with httpx.Client(timeout=10) as transport:
            yield Honcho(api_key=key, workspace_id=scope,
                         base_url=os.environ.get('HONCHO_URL', '').strip() or 'https://api.honcho.dev',
                         timeout=10, max_retries=0, http_client=transport)
    except MemoryError:
        raise
    except Exception as error:
        # Service messages can contain request data; never print them wholesale.
        status = getattr(error, 'status_code', None)
        detail = f' (HTTP {status})' if status else ''
        raise MemoryError(f'Honcho memory unavailable{detail}. Check your key, connection, and workspace permissions.') from error


def conversations(page: int = 1) -> tuple[list[str], bool]:
    scope = project_scope()
    try:
        with honcho_client(scope) as client:
            result = client.sessions(page=page, size=50)
            for session in result.items:
                local.remember_session(scope, session.id)
            remote = [session.id for session in result.items]
            if page == 1:
                remote = list(dict.fromkeys(local.sessions(scope) + remote))
            return remote, result.has_next_page()
    except MemoryError:
        ids = local.sessions(scope)
        return ids[(page-1)*50:page*50], len(ids) > page*50


def sync_pending(scope: str, session_id: str | None = None) -> str:
    """Replay in order; reconcile uncertain writes without blindly duplicating them."""
    with local.sync_lock() as acquired:
        if not acquired:
            return 'Memory: saved locally; another synchronization is running.'
        queued = local.pending(scope, session_id)
        if not queued:
            return 'Memory: no exchanges pending upload.'
        try:
            with honcho_client(scope) as client:
                for exchange in queued:
                    session = client.session(exchange['session'], metadata={
                        'mode': 'ask', 'synapse_session_id': exchange['session']}, peers=['user', 'synapse'])
                    remote = session.messages(filters={'metadata': {'synapse_exchange_id': exchange['id']}}, size=10)
                    found = {m.peer_id for m in remote.items
                             if (m.peer_id == 'user' and m.content == exchange['question'])
                             or (m.peer_id == 'synapse' and m.content == exchange['answer'])}
                    if found == {'user', 'synapse'}:
                        local.set_state(exchange['id'], 'synced')
                        continue
                    if exchange['state'] == 'uploading':
                        return 'Memory: saved locally; an earlier upload is unconfirmed. It will be checked again on the next sync; no duplicate upload was sent.'
                    messages = [client.peer(peer).message(exchange[key],
                                metadata={'synapse_exchange_id': exchange['id']}, created_at=exchange['created'])
                                for peer, key in [('user', 'question'), ('synapse', 'answer')] if peer not in found]
                    local.set_state(exchange['id'], 'uploading')
                    session.add_messages(messages)
                    local.set_state(exchange['id'], 'synced')
        except MemoryError:
            return f'Memory: offline or upload unconfirmed; saved locally · {len(local.pending(scope, session_id))} exchanges pending.'
        return 'Memory: sync complete; exchanges saved locally and in Honcho.'


def _bounded(messages):
    """Enforce a local size ceiling in addition to Honcho's token budget."""
    result = []
    remaining = MAX_CONTEXT_CHARS
    for message in reversed(messages):
        role, content = message.get('role'), message.get('content')
        if role not in ('user', 'assistant', 'system') or not isinstance(content, str):
            continue
        content = content[-remaining:]
        if not content:
            continue
        result.append({'role': role, 'content': content})
        remaining -= len(content)
        if remaining <= 0:
            break
    return list(reversed(result))


@dataclass
class Conversation:
    scope: str | None
    id: str
    temporary: bool = False
    history: list[dict[str, str]] = field(default_factory=list)
    status: str = "Memory: local SQLite history ready."

    def check_project(self):
        try:
            changed = not self.temporary and project_scope() != self.scope
        except ProjectError as error:
            raise MemoryError('Cannot read the active project. Resolve the registry error before continuing memory.') from error
        if changed:
            raise MemoryError('Active project changed. Open a conversation for the selected project.')

    def context(self, question):
        self.check_project()
        if self.temporary:
            return _bounded(self.history)
        self.status = sync_pending(self.scope, self.id)
        if local.pending(self.scope, self.id):
            return _bounded(local.context(self.scope, self.id))
        try:
            with honcho_client(self.scope) as client:
                context = client.session(self.id).context(tokens=CONTEXT_TOKENS, summary=True,
                                                          peer_target='user', search_query=question)
                messages = _bounded(context.to_openai(assistant='synapse'))
                # Preserve the recent local snapshot if remote context is temporarily empty.
                if messages:
                    local.cache_context(self.scope, self.id, messages)
                self.status = 'Memory: Honcho connected; local history available offline.'
                return messages or _bounded(local.context(self.scope, self.id))
        except MemoryError:
            self.status = 'Memory: Honcho offline; using local SQLite history.'
            return _bounded(local.context(self.scope, self.id))

    def save(self, question, answer):
        self.check_project()
        if self.temporary:
            self.history = _bounded(self.history + [{'role': 'user', 'content': question},
                                                    {'role': 'assistant', 'content': answer}])
            return
        local.append(self.scope, self.id, question, answer)
        self.status = sync_pending(self.scope, self.id)


def open_conversation(session_id: str | None = None, *, temporary=False) -> Conversation:
    if temporary:
        return Conversation(None, 'temporary-' + uuid4().hex[:12], temporary=True)
    scope = project_scope()
    if session_id and local.known(scope, session_id):
        return Conversation(scope, session_id)
    if session_id:
        with honcho_client(scope) as client:
            result = client.sessions(filters={'metadata': {'synapse_session_id': session_id}}, size=50)
            if not any(session.id == session_id for session in result.items):
                raise MemoryError('Conversation not found in the active project.')
    else:
        session_id = 'ask-' + uuid4().hex
    local.remember_session(scope, session_id)
    return Conversation(scope, session_id)
