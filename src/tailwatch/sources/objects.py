"""Content-addressed local objects with atomic creation and read verification."""

import hashlib
import os
import re
import tempfile
from pathlib import Path
from typing import Protocol


class ObjectStore(Protocol):
    def put(self, data: bytes) -> str: ...

    def get(self, digest: str) -> bytes: ...


class LocalObjectStore:
    def __init__(self, root: Path) -> None:
        self.root = root

    def _path(self, digest: str) -> Path:
        if not re.fullmatch(r"[0-9a-f]{64}", digest):
            raise ValueError("expected a SHA-256 digest")
        return self.root / digest[:2] / digest

    def put(self, data: bytes) -> str:
        digest = hashlib.sha256(data).hexdigest()
        target = self._path(digest)
        target.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(dir=target.parent, delete=False) as output:
            temporary = Path(output.name)
            try:
                output.write(data)
                output.flush()
                os.fsync(output.fileno())
            except BaseException:
                temporary.unlink(missing_ok=True)
                raise
        try:
            # Atomic create-if-absent: never replace an existing object.
            try:
                os.link(temporary, target)
            except FileExistsError:
                pass
            self.get(digest)
        finally:
            temporary.unlink(missing_ok=True)
        return digest

    def get(self, digest: str) -> bytes:
        data = self._path(digest).read_bytes()
        if hashlib.sha256(data).hexdigest() != digest:
            raise ValueError("object checksum mismatch")
        return data
