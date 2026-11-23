"""Read-only discovery and conservative three-way content reconciliation."""
import difflib
import os
from pathlib import Path
import re
import stat
import subprocess
import tempfile


def is_link(path):
    if not path.exists() and not path.is_symlink():
        return False
    return path.is_symlink() or bool(
        getattr(path.lstat(), 'st_file_attributes', 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT)


def valid_name(name):
    return bool(re.fullmatch(r'[a-z0-9]+(?:-[a-z0-9]+)*', name)) and len(name) <= 64


def discover(root):
    """Find skill folders without following links or descending into skill payloads."""
    root = Path(root)
    if not root.exists() or is_link(root):
        return []
    found = []
    for current, dirs, files in os.walk(root, followlinks=False):
        folder = Path(current)
        if 'SKILL.md' in files:
            found.append(folder)
            dirs[:] = []
        else:
            dirs[:] = sorted(d for d in dirs if not d.startswith('.') and
                             d != '__pycache__' and not is_link(folder / d))
    return sorted(found)


def text_content(data):
    if data is None:
        return ''
    if b'\0' in data or len(data) > 1024 * 1024:
        return None
    try:
        return data.decode('utf-8-sig')
    except UnicodeDecodeError:
        return None


def text_diff(before, after, left, right):
    a, b = text_content(before), text_content(after)
    if a is None or b is None:
        return {'kind': 'binary-or-large', 'diff': None}
    # Preserve missing-final-newline evidence in a human-readable unified diff.
    lines = list(difflib.unified_diff(a.replace('\r\n', '\n').splitlines(keepends=True),
                                    b.replace('\r\n', '\n').splitlines(keepends=True),
                                    fromfile=left, tofile=right))
    output = ''.join(line if line.endswith('\n') else line + '\n\\ No newline at end of file\n'
                     for line in lines)
    if not output and before != after:
        output = left + ' -> ' + right + ': byte-level difference (BOM, line endings or empty-file presence).\n'
    return {'kind': 'text', 'diff': output}


def merge_file(base, local, upstream):
    if local == upstream:
        return local, None
    if local == base:
        return upstream, None
    if upstream == base:
        return local, None
    if base is None:
        return None, 'Both sides added different contents.'
    if local is None or upstream is None:
        return None, 'One side deleted a file that the other side modified.'
    if any(text_content(data) is None for data in (base, local, upstream)):
        return None, 'Both sides changed a binary, non-UTF-8 or large file.'
    with tempfile.TemporaryDirectory(prefix='skillctl-merge-') as temp:
        paths = [Path(temp) / name for name in ('local', 'base', 'upstream')]
        for path, data in zip(paths, (local, base, upstream)):
            path.write_bytes(data)
        result = subprocess.run(['git', 'merge-file', '-p', '--diff3',
                                 '-L', 'LOCAL', '-L', 'BASE', '-L', 'UPSTREAM',
                                 *map(str, paths)], capture_output=True)
    if result.returncode == 0:
        return result.stdout, None
    if 0 < result.returncode <= 127:
        return result.stdout, 'Overlapping text edits; resolve the conflict before applying.'
    raise ValueError('git merge-file failed: ' + result.stderr.decode('utf-8', errors='replace'))


def merge_trees(base, local, upstream):
    output, conflicts = {}, []
    for name in sorted(base.keys() | local.keys() | upstream.keys()):
        merged, reason = merge_file(base.get(name), local.get(name), upstream.get(name))
        if reason:
            conflicts.append({'file': name, 'reason': reason,
                              'preview': text_content(merged) if merged is not None else None})
        elif merged is not None:
            output[name] = merged
    # A file-to-directory replacement must not partially overwrite another file.
    canonical = {}
    for name in output:
        identity = os.path.normcase(name)
        if identity in canonical:
            conflicts.append({'file': name, 'reason': 'Filename collision on this filesystem.', 'preview': None})
        canonical[identity] = name
    for name in output:
        if any(os.path.normcase(parent.as_posix()) in canonical for parent in Path(name).parents if str(parent) != '.'):
            conflicts.append({'file': name, 'reason': 'File/directory collision.', 'preview': None})
    return output, conflicts
