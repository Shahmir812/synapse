from pathlib import Path
import json

import pytest

from synapse import file_context as context


def test_multiple_files_unicode_empty_and_duplicate(tmp_path):
    (tmp_path / 'one.py').write_text('print("こんにちは")\n')
    (tmp_path / 'empty.txt').write_text('')
    files = context.load_attachments(['one.py', './one.py', tmp_path / 'empty.txt'], tmp_path)
    assert [f.path for f in files] == ['one.py', 'empty.txt']
    assert files[0].size == len(files[0].content.encode())
    prompt = context.attach_to_question('Explain', files)
    assert 'こんにちは' in prompt
    assert 'one.py' in prompt and 'empty.txt' in prompt
    assert context.attach_to_question('Explain', []) == 'Explain'


@pytest.mark.parametrize('name', ['.env', '.env.local', 'server.pem', 'private.key', 'credentials.json', '.git/config', '.synapse/state.json'])
def test_excluded_files(tmp_path, name):
    path = tmp_path / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text('secret')
    with pytest.raises(context.FileContextError, match='excluded'):
        context.load_attachments([name], tmp_path)


@pytest.mark.parametrize('kind', ['missing', 'directory', 'outside', 'symlink_outside', 'symlink_secret', 'binary', 'encoding', 'large'])
def test_invalid_attachments(tmp_path, kind):
    root = tmp_path / 'workspace'
    root.mkdir()
    path = root / 'file.txt'
    if kind == 'directory':
        path.mkdir()
    elif kind in ('outside', 'symlink_outside'):
        outside = tmp_path / 'outside.txt'
        outside.write_text('outside')
        if kind == 'outside':
            path = outside
        else:
            path.symlink_to(outside)
    elif kind == 'symlink_secret':
        secret = root / '.env'
        secret.write_text('secret')
        path.symlink_to(secret)
    elif kind == 'binary':
        path.write_bytes(b'abc\x00def')
    elif kind == 'encoding':
        path.write_bytes(b'\xff')
    elif kind == 'large':
        path.write_bytes(b'x' * (context.MAX_FILE_BYTES + 1))
    with pytest.raises(context.FileContextError):
        context.load_attachments([path], root)


def test_combined_and_count_limits(tmp_path):
    paths = []
    for i in range(11):
        path = tmp_path / f'{i}.txt'
        path.write_text('x' * context.MAX_FILE_BYTES)
        paths.append(path)
    assert len(context.load_attachments(paths[:4], tmp_path)) == 4
    with pytest.raises(context.FileContextError, match='combined'):
        context.load_attachments(paths[:5], tmp_path)
    for path in paths:
        path.write_text('x')
    with pytest.raises(context.FileContextError, match='at most'):
        context.load_attachments(paths, tmp_path)


def test_picker_filters_and_sorts(tmp_path):
    for name, content in [('b.py', b'print(1)'), ('a.md', b'# Hello'), ('.env', b'secret'), ('image.bin', b'\x00\xff')]:
        (tmp_path / name).write_bytes(content)
    (tmp_path / 'node_modules').mkdir()
    (tmp_path / 'node_modules' / 'ignored.js').write_text('ignored')
    (tmp_path / 'alias.py').symlink_to(tmp_path / 'b.py')
    assert context.selectable_files(tmp_path) == (['a.md', 'b.py'], False)


def test_picker_is_bounded(tmp_path):
    for i in range(502):
        (tmp_path / f'{i}.txt').write_text('x')
    candidates, truncated = context.selectable_files(tmp_path)
    assert len(candidates) == 500
    assert truncated


def test_read_failure_is_readable(tmp_path, monkeypatch):
    path = tmp_path / 'file.txt'
    path.write_text('text')
    def denied(*args, **kwargs):
        raise PermissionError('denied')
    monkeypatch.setattr(Path, 'open', denied)
    with pytest.raises(context.FileContextError, match='Cannot read'):
        context.load_attachments([path], tmp_path)
