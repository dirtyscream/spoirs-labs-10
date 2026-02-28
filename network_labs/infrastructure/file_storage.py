import os
import threading
from typing import Dict

from network_labs.domain.interfaces import FileStore


class LocalFileStore(FileStore):
    def __init__(self, base_dir: str) -> None:
        self._base_dir = base_dir
        self._locks: Dict[str, threading.Lock] = {}
        self._meta_lock = threading.Lock()
        os.makedirs(base_dir, exist_ok=True)

    def read_bytes(self, filename: str, offset: int, size: int) -> bytes:
        path = self._resolve(filename)
        with self._lock_for(filename):
            with open(path, "rb") as f:
                f.seek(offset)
                return f.read(size)

    def write_bytes(self, filename: str, offset: int, data: bytes) -> None:
        path = self._resolve(filename)
        with self._lock_for(filename):
            mode = "r+b" if os.path.exists(path) else "wb"
            with open(path, mode) as f:
                f.seek(offset)
                f.write(data)

    def get_size(self, filename: str) -> int:
        path = self._resolve(filename)
        if not os.path.exists(path):
            return 0
        return os.path.getsize(path)

    def exists(self, filename: str) -> bool:
        return os.path.exists(self._resolve(filename))

    def delete_file(self, filename: str) -> None:
        path = self._resolve(filename)
        with self._lock_for(filename):
            if os.path.exists(path):
                os.remove(path)

    def _resolve(self, filename: str) -> str:
        return os.path.join(self._base_dir, os.path.basename(filename))

    def _lock_for(self, filename: str) -> threading.Lock:
        with self._meta_lock:
            if filename not in self._locks:
                self._locks[filename] = threading.Lock()
            return self._locks[filename]
