"""Bounded process-local caches; disk snapshots remain the source of truth."""
from collections import OrderedDict
from copy import deepcopy
from pathlib import Path
from threading import Lock
import json


class JsonFileCache:
    def __init__(self, max_entries=256):
        self._entries = OrderedDict()
        self._lock = Lock()
        self._max_entries = max_entries

    @staticmethod
    def _signature(path):
        stat = path.stat()
        return (str(path), stat.st_mtime_ns, stat.st_size, stat.st_ino, stat.st_ctime_ns)

    def snapshot(self, path):
        """Return (file signature, shared JSON object); callers must not mutate it.

        Inspect every access, including atomic replacements and in-place writes
        that restore mtime. Never pair old contents with a newer signature.
        """
        path = Path(path).resolve()
        with self._lock:
            try:
                for _ in range(2):
                    signature = self._signature(path)
                    cached = self._entries.get(path)
                    if cached is not None and cached[0] == signature:
                        self._entries.move_to_end(path)
                        return cached
                    try:
                        value = json.loads(path.read_text())
                        if not isinstance(value, dict):
                            value = {}
                    except ValueError:
                        value = {}
                    if self._signature(path) != signature:
                        continue
                    entry = (signature, value)
                    self._entries[path] = entry
                    self._entries.move_to_end(path)
                    while len(self._entries) > self._max_entries:
                        self._entries.popitem(last=False)
                    return entry
            except OSError:
                pass
            self._entries.pop(path, None)
            return None, {}

    def read(self, path):
        """Return a private copy for consumers that update cached documents."""
        return deepcopy(self.snapshot(path)[1])


class ResultCache:
    def __init__(self, max_entries=8):
        self._entries = OrderedDict()
        self._lock = Lock()
        self._max_entries = max_entries

    def get_or_build(self, key, build):
        """Deduplicate work, bound memory, and isolate each caller's mutations."""
        with self._lock:
            if key not in self._entries:
                self._entries[key] = deepcopy(build())
                while len(self._entries) > self._max_entries:
                    self._entries.popitem(last=False)
            self._entries.move_to_end(key)
            result = self._entries[key]
        return deepcopy(result)
