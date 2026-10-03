#!/usr/bin/env python3
"""No-follow, descriptor-relative helpers for Native acceptance outputs."""
import hashlib
import os
import secrets
import stat
from pathlib import Path


_DIRECTORY_FLAGS = os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC | os.O_NOFOLLOW
_FILE_FLAGS = os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW


def _component(name):
    if not name or name in {'.', '..'} or '/' in name or '\x00' in name:
        raise ValueError('unsafe output path component')
    return name


def _open_directory(path, *, create=False):
    path = Path(os.fspath(path))
    if '..' in path.parts:
        raise ValueError('parent traversal in output path')
    absolute = Path(os.path.abspath(path))
    if not absolute.is_absolute():
        raise ValueError('output path must be absolute')

    directory = os.open(absolute.anchor, _DIRECTORY_FLAGS)
    try:
        for raw_name in absolute.parts[1:]:
            name = _component(raw_name)
            try:
                child = os.open(name, _DIRECTORY_FLAGS, dir_fd=directory)
            except FileNotFoundError:
                if not create:
                    raise
                try:
                    os.mkdir(name, 0o700, dir_fd=directory)
                except FileExistsError:
                    pass
                child = os.open(name, _DIRECTORY_FLAGS, dir_fd=directory)
                os.fchmod(child, 0o700)
            info = os.fstat(child)
            if not stat.S_ISDIR(info.st_mode) or info.st_uid not in {0, os.getuid()}:
                os.close(child)
                raise ValueError('output parent is not an owned real directory')
            os.close(directory)
            directory = child
        return directory
    except BaseException:
        os.close(directory)
        raise


def ensure_directory(path):
    """Create a directory chain without following any symlink component."""
    directory = _open_directory(path, create=True)
    os.close(directory)


def _validate_regular(info, *, max_bytes, expected_size=None):
    if (
        not stat.S_ISREG(info.st_mode)
        or info.st_uid != os.getuid()
        or info.st_nlink != 1
        or info.st_size > max_bytes
        or (expected_size is not None and info.st_size != expected_size)
    ):
        raise ValueError('unsafe Native acceptance file')


def _open_regular(path, *, max_bytes, expected_size=None):
    path = Path(path)
    directory = _open_directory(path.parent)
    try:
        name = _component(path.name)
        before = os.stat(name, dir_fd=directory, follow_symlinks=False)
        _validate_regular(before, max_bytes=max_bytes, expected_size=expected_size)
        file_fd = os.open(name, _FILE_FLAGS, dir_fd=directory)
        after = os.fstat(file_fd)
        _validate_regular(after, max_bytes=max_bytes, expected_size=expected_size)
        if (before.st_dev, before.st_ino) != (after.st_dev, after.st_ino):
            os.close(file_fd)
            raise ValueError('Native acceptance file changed while opening')
        return directory, file_fd, after
    except BaseException:
        os.close(directory)
        raise


def _check_destination(directory, name, *, max_bytes):
    try:
        info = os.stat(name, dir_fd=directory, follow_symlinks=False)
    except FileNotFoundError:
        return
    _validate_regular(info, max_bytes=max_bytes)


def _write_all(file_fd, data):
    view = memoryview(data)
    while view:
        count = os.write(file_fd, view)
        if count <= 0:
            raise OSError('short write to Native acceptance output')
        view = view[count:]


def _atomic_output(path, *, max_bytes, writer):
    path = Path(path)
    directory = _open_directory(path.parent)
    name = _component(path.name)
    temporary = f'.{name}.tmp-{secrets.token_hex(12)}'
    file_fd = None
    created = False
    try:
        _check_destination(directory, name, max_bytes=max_bytes)
        file_fd = os.open(
            temporary,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_CLOEXEC | os.O_NOFOLLOW,
            0o600,
            dir_fd=directory,
        )
        created = True
        writer(file_fd)
        info = os.fstat(file_fd)
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_nlink != 1:
            raise ValueError('unsafe temporary acceptance output')
        if info.st_size > max_bytes:
            raise ValueError('Native acceptance output exceeds its size bound')
        os.fsync(file_fd)
        os.close(file_fd)
        file_fd = None
        # A concurrent replacement is never followed: rename replaces a link
        # entry itself. Rechecking also makes ordinary pre-existing links fail.
        _check_destination(directory, name, max_bytes=max_bytes)
        os.replace(temporary, name, src_dir_fd=directory, dst_dir_fd=directory)
        os.fsync(directory)
    finally:
        if file_fd is not None:
            os.close(file_fd)
        if created:
            try:
                os.unlink(temporary, dir_fd=directory)
            except FileNotFoundError:
                pass
        os.close(directory)


def safe_write_text(path, text, *, max_bytes=4 * 1024 * 1024):
    data = text.encode('utf-8')
    if len(data) > max_bytes:
        raise ValueError('Native acceptance text exceeds its size bound')
    _atomic_output(path, max_bytes=max_bytes, writer=lambda fd: _write_all(fd, data))


def safe_copy_file(source, destination, *, max_bytes, expected_size=None):
    source_directory, source_fd, source_info = _open_regular(
        source, max_bytes=max_bytes, expected_size=expected_size
    )
    try:
        def writer(destination_fd):
            copied = 0
            while True:
                chunk = os.read(source_fd, min(1024 * 1024, max_bytes - copied + 1))
                if not chunk:
                    break
                copied += len(chunk)
                if copied > max_bytes:
                    raise ValueError('Native acceptance source exceeds its size bound')
                _write_all(destination_fd, chunk)
            if expected_size is not None and copied != expected_size:
                raise ValueError('Native acceptance source changed size while copying')
            if os.fstat(source_fd).st_size != source_info.st_size:
                raise ValueError('Native acceptance source changed while copying')

        _atomic_output(destination, max_bytes=max_bytes, writer=writer)
    finally:
        os.close(source_fd)
        os.close(source_directory)


def safe_digest(path, *, max_bytes, expected_size=None):
    directory, file_fd, info = _open_regular(
        path, max_bytes=max_bytes, expected_size=expected_size
    )
    try:
        digest = hashlib.sha256()
        total = 0
        while True:
            chunk = os.read(file_fd, min(1024 * 1024, max_bytes - total + 1))
            if not chunk:
                break
            total += len(chunk)
            if total > max_bytes:
                raise ValueError('Native acceptance source exceeds its size bound')
            digest.update(chunk)
        if total != info.st_size or (expected_size is not None and total != expected_size):
            raise ValueError('Native acceptance source changed size while reading')
        return digest.hexdigest()
    finally:
        os.close(file_fd)
        os.close(directory)


def safe_read_text(path, *, max_bytes=1024 * 1024):
    directory, file_fd, _ = _open_regular(path, max_bytes=max_bytes)
    try:
        chunks = []
        remaining = max_bytes + 1
        while remaining:
            chunk = os.read(file_fd, min(64 * 1024, remaining))
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        data = b''.join(chunks)
        if len(data) > max_bytes:
            raise ValueError('Native acceptance text exceeds its size bound')
        return data.decode('utf-8')
    finally:
        os.close(file_fd)
        os.close(directory)
