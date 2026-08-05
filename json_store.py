# -*- coding: UTF-8 -*-
"""Thread-safe helpers for the application's mutable JSON files."""

import json
import os
import tempfile
import threading
from contextlib import contextmanager


_locks_guard = threading.Lock()
_file_locks: dict[str, threading.RLock] = {}


def _path_key(path: str | os.PathLike) -> str:
    return os.path.normcase(os.path.abspath(os.fspath(path)))


def _get_lock(path: str | os.PathLike) -> threading.RLock:
    key = _path_key(path)
    with _locks_guard:
        return _file_locks.setdefault(key, threading.RLock())


@contextmanager
def json_file_lock(path: str | os.PathLike):
    """Lock all in-process access to one JSON file.

    The lock is re-entrant so a read-modify-write transaction can call the
    regular read/write helpers while retaining exclusive access.
    """
    lock = _get_lock(path)
    with lock:
        yield


def read_json(path: str | os.PathLike):
    """Read and decode a JSON file while holding its path lock."""
    with json_file_lock(path):
        with open(path, "r", encoding="utf-8") as file:
            return json.load(file)


def write_json(path: str | os.PathLike, data, *, indent: int = 2) -> None:
    """Atomically encode a JSON value while holding its path lock."""
    path = os.path.abspath(os.fspath(path))
    directory = os.path.dirname(path)
    with json_file_lock(path):
        os.makedirs(directory, exist_ok=True)
        descriptor, temporary_path = tempfile.mkstemp(
            prefix=f".{os.path.basename(path)}.",
            suffix=".tmp",
            dir=directory,
        )
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as file:
                json.dump(data, file, ensure_ascii=False, indent=indent)
                file.flush()
                os.fsync(file.fileno())
            os.replace(temporary_path, path)
        except Exception:
            try:
                os.remove(temporary_path)
            except OSError:
                pass
            raise
